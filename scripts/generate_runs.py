#!/usr/bin/env python
"""
Batch-generate retrieval runs for evaluation.

Produces:
  data/eval/runs_dense.jsonl    – dense-only (Qdrant)
  data/eval/runs_sparse.jsonl   – sparse-only (BM25)
  data/eval/runs_hybrid.jsonl   – hybrid RRF (dense + sparse)

Usage:
    python -m scripts.generate_runs [--config configs/default.yaml]
                                    [--queries data/eval/queries.jsonl]
                                    [--top_k 10]
                                    [--rrf_k 60]
"""
import argparse
import pickle
import re
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

from qdrant_client import QdrantClient
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer

from scripts.utils import load_config, read_jsonl, save_jsonl, vnfold


def reciprocal_rank_fusion(rankings: list[list[str]], k: int = 60) -> list[str]:
    scores: dict[str, float] = {}
    for rlist in rankings:
        for rank, doc_id in enumerate(rlist):
            scores[doc_id] = scores.get(doc_id, 0) + 1.0 / (k + rank + 1)
    return [doc for doc, _ in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)]


def tokenize(text: str, tok_type: str) -> list[str]:
    if tok_type == "vi_basic":
        return re.findall(r"[a-z0-9]+", vnfold(text))
    return text.lower().split()


def make_rows(qid: str, doc_ids: list[str], scores: list[float] | None = None) -> list[dict]:
    rows = []
    for rank, did in enumerate(doc_ids, start=1):
        row: dict = {"query_id": qid, "doc_id": did, "rank": rank}
        if scores and rank - 1 < len(scores):
            row["score"] = round(scores[rank - 1], 6)
        rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser(description="Batch retrieval run generator")
    parser.add_argument("--config",  default="configs/default.yaml")
    parser.add_argument("--queries", default="data/eval/queries.jsonl")
    parser.add_argument("--top_k",  type=int, default=None)
    parser.add_argument("--rrf_k",  type=int, default=None)
    args = parser.parse_args()

    cfg    = load_config(args.config)
    top_k  = args.top_k  or cfg["search"]["top_k"]
    rrf_k  = args.rrf_k  or cfg["search"]["rrf_k"]

    queries = read_jsonl(args.queries)
    print(f"Queries: {len(queries)}")

    # ── Dense model + client ──────────────────────────────────────────────────
    print("Loading embedding model…")
    model   = SentenceTransformer(cfg["embedding"]["model_name"])
    qclient = QdrantClient(
        url=cfg["qdrant"]["url"],
        api_key=cfg["qdrant"].get("api_key") or None,
    )
    normalize = cfg["embedding"].get("normalize", True)
    coll = cfg["qdrant"]["collection"]

    # ── BM25 ─────────────────────────────────────────────────────────────────
    bm25_path = Path(cfg["paths"]["bm25_index_path"])
    print(f"Loading BM25 index from {bm25_path}…")
    with bm25_path.open("rb") as fh:
        obj = pickle.load(fh)
    bm25:    BM25Okapi = obj["bm25"]
    metas:   list      = obj["metas"]
    tok_type: str      = obj.get("tokenizer", cfg["bm25"]["tokenizer"])

    # ── Containers ────────────────────────────────────────────────────────────
    rows_dense:  list[dict] = []
    rows_sparse: list[dict] = []
    rows_hybrid: list[dict] = []

    for i, q in enumerate(queries, 1):
        qid   = q["query_id"]
        query = q["query"]
        if i % 50 == 0 or i == 1:
            print(f"  [{i}/{len(queries)}] {query[:60]}…")

        # Dense
        qvec   = model.encode([query], normalize_embeddings=normalize)[0]
        dhits  = qclient.search(
            collection_name=coll,
            query_vector=qvec.tolist(),
            limit=top_k,
            with_payload=True,
        )
        d_ids    = [h.payload["doc_id"] for h in dhits]
        d_scores = [float(h.score) for h in dhits]
        d_map    = {h.payload["doc_id"]: float(h.score) for h in dhits}

        rows_dense.extend(make_rows(qid, d_ids, d_scores))

        # Sparse
        q_tok  = tokenize(query, tok_type)
        scrs   = bm25.get_scores(q_tok)
        order  = sorted(range(len(scrs)), key=lambda idx: scrs[idx], reverse=True)[:top_k]
        s_ids  = [metas[idx]["doc_id"] for idx in order]
        s_scrs = [float(scrs[idx]) for idx in order]
        s_map  = {metas[idx]["doc_id"]: float(scrs[idx]) for idx in order}

        rows_sparse.extend(make_rows(qid, s_ids, s_scrs))

        # Hybrid RRF
        fused = reciprocal_rank_fusion([d_ids, s_ids], k=rrf_k)[:top_k]
        h_scores = [d_map.get(did, 0.0) + s_map.get(did, 0.0) for did in fused]
        rows_hybrid.extend(make_rows(qid, fused, h_scores))

    # ── Save ──────────────────────────────────────────────────────────────────
    out_dir = Path("data/eval")
    out_dir.mkdir(parents=True, exist_ok=True)

    save_jsonl(rows_dense,  out_dir / "runs_dense.jsonl")
    save_jsonl(rows_sparse, out_dir / "runs_sparse.jsonl")
    save_jsonl(rows_hybrid, out_dir / "runs_hybrid.jsonl")

    print(f"\nSaved:")
    print(f"  runs_dense.jsonl  : {len(rows_dense)} rows")
    print(f"  runs_sparse.jsonl : {len(rows_sparse)} rows")
    print(f"  runs_hybrid.jsonl : {len(rows_hybrid)} rows")


if __name__ == "__main__":
    main()
