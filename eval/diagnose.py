"""Failure-mode diagnosis: where does accuracy actually leak?

Improving retrieval without this is guesswork. A miss at k=5 has three very
different causes, and each demands a different fix:

  unreachable   the gold chunk is absent from a deep candidate pool
                → the loss is in chunking or embedding, not ranking
  ranking       the gold chunk is in the pool but ranked below the cutoff
                → the loss is in fusion / reranking
  resolved      already correct at k=5

The script also separates document routing from clause resolution. If the
right document is retrieved but the wrong clause within it is ranked first,
the fix is a within-document stage, not a better global retriever.

No cross-encoder is used, so this runs in seconds rather than an hour.

Run from project root:
  python eval/diagnose.py --gt eval/test_queries_gt_100_strict.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval._env import load_env  # noqa: E402

load_env()

from src.config import load_config  # noqa: E402
from src.retrieval.bm25_retriever import BM25Retriever  # noqa: E402
from src.retrieval.hybrid_retriever import reciprocal_rank_fusion  # noqa: E402
from src.retrieval.qdrant_params import search_params  # noqa: E402
from src.retrieval.vector_store import VectorRetriever  # noqa: E402

CACHE_DIR = ROOT / "eval" / "cache"


def first_rank(ids: list[str], gold: set[str]) -> int | None:
    for i, cid in enumerate(ids, start=1):
        if cid in gold:
            return i
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description="Retrieval failure-mode diagnosis")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--gt", default="eval/test_queries_gt_100_strict.jsonl")
    ap.add_argument("--deep", type=int, default=100, help="depth of the candidate pool")
    args = ap.parse_args()

    cfg = load_config(str(ROOT / args.config))
    gt_path = ROOT / args.gt
    rows = [
        json.loads(line)
        for line in gt_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    graded = [r for r in rows if r.get("relevant_ids")]
    print(f"Ground truth: {args.gt}")
    print(f"{len(graded)}/{len(rows)} queries have labels\n")

    # chunk_id -> source document, for the routing-vs-resolution split
    cid2src: dict[str, str] = {}
    for path in (ROOT / "data" / "processed").glob("*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                d = json.loads(line)
                cid2src[d["chunk_id"]] = d.get("source", "")

    dense = VectorRetriever(cfg)
    sparse = BM25Retriever(cfg)
    hnsw = search_params({**cfg["vector_store"], "search": {"hnsw_ef": 128, "exact": False}})

    qvecs = None
    if (CACHE_DIR / "query_vectors.npy").exists():
        meta = json.loads((CACHE_DIR / "query_meta.json").read_text(encoding="utf-8"))
        by_q = {m["question"]: i for i, m in enumerate(meta)}
        arr = np.load(CACHE_DIR / "query_vectors.npy")
        qvecs = (by_q, arr)

    depth = args.deep
    cutoffs = (1, 3, 5, 10, 30, depth)
    stats = {name: Counter() for name in ("bm25", "dense", "fused")}
    causes: Counter = Counter()
    doc_hit = Counter()
    rank_examples: list[tuple[int, str]] = []

    for r in graded:
        q = r["question"]
        gold = set(r["relevant_ids"])
        gold_srcs = {cid2src.get(c, "") for c in gold} - {""}

        vec = None
        if qvecs and q in qvecs[0]:
            vec = qvecs[1][qvecs[0][q]].tolist()

        d = dense.search(q, top_k=depth, vector=vec, params=hnsw)
        s = sparse.search(q, top_k=depth)
        f = reciprocal_rank_fusion([d, s], id_key="_id", k=cfg["retrieval"]["rrf_k"])

        lists = {
            "bm25": [x.get("chunk_id") or x.get("_id") for x in s],
            "dense": [x.get("chunk_id") or x.get("_id") for x in d],
            "fused": [x.get("chunk_id") or x.get("_id") for x in f],
        }
        for name, ids in lists.items():
            rank = first_rank(ids, gold)
            for k in cutoffs:
                if rank is not None and rank <= k:
                    stats[name][k] += 1

        # Did we at least reach the right document, and how early?
        fused_ids = lists["fused"]
        doc_rank = None
        for i, cid in enumerate(fused_ids[:depth], start=1):
            if cid2src.get(cid, "") in gold_srcs:
                doc_rank = i
                break
        if doc_rank is not None:
            for k in (1, 5, 10):
                if doc_rank <= k:
                    doc_hit[k] += 1

        rank = first_rank(fused_ids, gold)
        if rank is None:
            causes["unreachable (not in pool)"] += 1
        elif rank <= 5:
            causes["resolved @5"] += 1
        else:
            causes["ranking loss (in pool, rank >5)"] += 1
            rank_examples.append((rank, q[:60]))

    n = len(graded)

    def pct(c: int) -> str:
        return f"{c/n:6.1%}"

    print("── Recall by depth (first-stage, no reranker) ────────────")
    header = "retriever   " + "".join(f"{'@'+str(k):>9}" for k in cutoffs)
    print(header)
    for name in ("bm25", "dense", "fused"):
        line = f"{name:<12}" + "".join(f"{stats[name][k]/n:9.3f}" for k in cutoffs)
        print(line)

    print("\n── Document routing vs clause resolution (fused) ─────────")
    print(f"correct DOCUMENT reached @1 : {doc_hit[1]/n:.3f}")
    print(f"correct DOCUMENT reached @5 : {doc_hit[5]/n:.3f}")
    print(f"correct CLAUSE   reached @5 : {stats['fused'][5]/n:.3f}")
    gap = doc_hit[5] / n - stats["fused"][5] / n
    print(f"→ routing-resolution gap    : {gap:.3f}")
    if gap > 0.1:
        print("   The document is found but the clause inside it is not ranked;")
        print("   a within-document stage targets this directly.")

    print("\n── Failure attribution @5 (fused pool) ───────────────────")
    for cause, count in causes.most_common():
        print(f"{cause:<34}{count:>4}  {pct(count)}")

    ceiling = stats["fused"][depth] / n
    print(f"\nFirst-stage ceiling @{depth}: {ceiling:.3f}")
    print(f"  No reranker can exceed this. Headroom above current @5: "
          f"{ceiling - stats['fused'][5]/n:.3f}")

    if rank_examples:
        rank_examples.sort()
        print("\n── Recoverable near-misses (rank 6-30) ───────────────────")
        for rank, q in rank_examples[:10]:
            if rank <= 30:
                print(f"  rank {rank:>3}  {q}")


if __name__ == "__main__":
    main()
