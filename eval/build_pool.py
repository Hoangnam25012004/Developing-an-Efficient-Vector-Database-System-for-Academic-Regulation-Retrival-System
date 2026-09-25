"""Build a shallow judging pool, TREC-style but sized for one annotator.

Sanderson & Zobel (SIGIR 2005) found that for a fixed assessor budget, a
collection with more topics judged shallowly discriminates between systems
better than fewer topics judged deeply. This script implements that: every
topic keeps its full question set, and only the top of each run is pooled.

The pool draws from three runs that fail differently — the reranked hybrid
(what the system actually returns), BM25 (lexical), and dense (semantic).
Pooling from one run would bias the collection toward whatever that run is
good at, which is the flaw in seeding a pool from a single heuristic.

Units are shuffled within a topic before being written. Judging in rank order
invites the assessor to anchor on the system's opinion; a fixed seed keeps the
shuffle reproducible.

Run from project root (~30 min: the cross-encoder is the slow part):
  python eval/build_pool.py
  python eval/build_pool.py --depth-rerank 10 --depth-side 3
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval._env import load_env  # noqa: E402

load_env()

from src.chat.rag_chain import RAGChain  # noqa: E402
from src.config import load_config  # noqa: E402
from src.retrieval.bm25_retriever import BM25Retriever  # noqa: E402
from src.retrieval.qdrant_params import search_params  # noqa: E402
from src.retrieval.vector_store import VectorRetriever  # noqa: E402

CACHE_DIR = ROOT / "eval" / "cache"


def locator(chunk: dict) -> str:
    """Human-readable position, so the assessor can see what they are judging."""
    bits = []
    if chunk.get("chapter"):
        bits.append(f"Chương {chunk['chapter']}")
    if chunk.get("article"):
        bits.append(f"Điều {chunk['article']}")
    if chunk.get("khoan"):
        bits.append(f"Khoản {chunk['khoan']}")
    if not bits and chunk.get("section_type"):
        bits.append(str(chunk["section_type"]))
    return ", ".join(bits)


def main() -> None:
    ap = argparse.ArgumentParser(description="Build a shallow judging pool")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--gt", default=None, help="source of topics (default: config)")
    ap.add_argument("--depth-rerank", type=int, default=10)
    ap.add_argument("--depth-side", type=int, default=3,
                    help="depth taken from BM25 and dense, to widen the pool")
    ap.add_argument("--double-frac", type=float, default=0.2,
                    help="fraction of topics flagged for a second judging pass (kappa)")
    ap.add_argument("--limit", type=int, default=0, help="stop after N topics (testing)")
    ap.add_argument("--out", default="eval/pool.jsonl")
    ap.add_argument("--seed", type=int, default=13)
    args = ap.parse_args()

    cfg = load_config(str(ROOT / args.config))
    gt_path = ROOT / (args.gt or cfg["evaluation"]["test_queries_path"])
    topics = [
        json.loads(line)
        for line in gt_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if args.limit:
        topics = topics[: args.limit]

    print(f"Topics: {len(topics)}  (from {gt_path.name})")
    print("Loading pipeline… (cross-encoder load takes a moment)")
    chain = RAGChain(str(ROOT / args.config))
    dense = VectorRetriever(cfg)
    sparse = BM25Retriever(cfg)
    hnsw = search_params(
        {**cfg["vector_store"], "search": {"hnsw_ef": 128, "exact": False}}
    )

    qvecs = None
    if (CACHE_DIR / "query_vectors.npy").exists():
        meta = json.loads((CACHE_DIR / "query_meta.json").read_text(encoding="utf-8"))
        qvecs = (
            {m["question"]: i for i, m in enumerate(meta)},
            np.load(CACHE_DIR / "query_vectors.npy"),
        )

    rng = random.Random(args.seed)
    double_ids = set(
        rng.sample(
            [t.get("id", i + 1) for i, t in enumerate(topics)],
            max(int(len(topics) * args.double_frac), 1),
        )
    )

    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    records = []
    started = time.perf_counter()

    for i, topic in enumerate(topics, start=1):
        question = topic["question"]
        topic_id = topic.get("id", i)

        vec = None
        if qvecs and question in qvecs[0]:
            vec = qvecs[1][qvecs[0][question]].tolist()

        runs = {
            "hybrid-rerank": (chain.retrieve(question), args.depth_rerank),
            "bm25": (sparse.search(question, top_k=args.depth_side), args.depth_side),
            "dense": (
                dense.search(question, top_k=args.depth_side, vector=vec, params=hnsw),
                args.depth_side,
            ),
        }

        contributors: dict[str, set[str]] = {}
        by_id: dict[str, dict] = {}
        for run_name, (docs, depth) in runs.items():
            for d in docs[:depth]:
                cid = d.get("chunk_id") or d.get("_id")
                if not cid:
                    continue
                contributors.setdefault(cid, set()).add(run_name)
                by_id.setdefault(cid, d)

        units = []
        for cid, from_runs in contributors.items():
            d = by_id.get(cid)
            if not d:
                continue
            units.append(
                {
                    "chunk_id": cid,
                    # Kept alongside chunk_id so the qrels survive re-chunking:
                    # chunk_id is a content hash and changes when boundaries move.
                    "source": d.get("source", ""),
                    "article": d.get("article", ""),
                    "khoan": d.get("khoan", ""),
                    "page": d.get("page"),
                    "section_type": d.get("section_type", ""),
                    "locator": locator(d),
                    "text": (d.get("text") or "").strip(),
                    "runs": sorted(from_runs),
                }
            )

        rng.shuffle(units)
        records.append(
            {
                "topic_id": topic_id,
                "question": question,
                "reference_answer": topic.get("answer", ""),
                "double_judge": topic_id in double_ids,
                "units": units,
            }
        )
        if i % 10 == 0 or i == len(topics):
            elapsed = time.perf_counter() - started
            print(
                f"  [{i}/{len(topics)}] pooled  "
                f"({elapsed:.0f}s, {elapsed/i:.1f}s/topic)"
            )

    with out_path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    sizes = [len(r["units"]) for r in records]
    print(f"\nPool → {out_path}")
    print(f"  topics            {len(records)}")
    print(f"  units/topic       mean {sum(sizes)/len(sizes):.1f}, "
          f"min {min(sizes)}, max {max(sizes)}")
    print(f"  total judgments   {sum(sizes)}")
    print(f"  double-judged     {sum(1 for r in records if r['double_judge'])} topics")
    print(f"\nEstimated effort  ~{sum(sizes) * 7 / 3600:.1f} h at 7 s per judgment")
    print("\nNext:  python eval/judge.py")


if __name__ == "__main__":
    main()
