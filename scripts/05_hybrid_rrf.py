#!/usr/bin/env python
import argparse
import pickle
from pathlib import Path
from typing import List

from qdrant_client import QdrantClient
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer
from scripts.utils import load_config, vnfold
import regex as re


def reciprocal_rank_fusion(rankings: List[List[str]], k: int = 60) -> List[str]:
    scores = {}
    for rlist in rankings:
        for rank, doc_id in enumerate(rlist):
            scores[doc_id] = scores.get(doc_id, 0) + 1.0 / (k + rank + 1)
    return [doc for doc, _ in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)]


def main():
    parser = argparse.ArgumentParser(description="Hybrid RRF: BM25 + Dense")
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--query", required=True)
    parser.add_argument("--top_k", type=int, default=None)
    parser.add_argument("--rrf_k", type=int, default=None)
    args = parser.parse_args()

    cfg = load_config(args.config)
    top_k = args.top_k or cfg["search"]["top_k"]
    rrf_k = args.rrf_k or cfg["search"]["rrf_k"]

    # Dense via Qdrant
    model = SentenceTransformer(cfg["embedding"]["model_name"])
    qclient = QdrantClient(url=cfg["qdrant"]["url"], api_key=cfg["qdrant"].get("api_key") or None)
    qvec = model.encode([args.query], normalize_embeddings=cfg["embedding"].get("normalize", True))[
        0
    ]
    dhits = qclient.search(
        collection_name=cfg["qdrant"]["collection"],
        query_vector=qvec.tolist(),
        limit=top_k,
        with_payload=True,
    )
    d_ids = [h.payload["doc_id"] for h in dhits]
    d_map = {h.payload["doc_id"]: h.payload | {"_dense_score": float(h.score)} for h in dhits}

    # Sparse via BM25
    with open(Path(cfg["paths"]["bm25_index_path"]), "rb") as f:
        obj = pickle.load(f)
    bm25: BM25Okapi = obj["bm25"]
    metas = obj["metas"]
    tok = obj.get("tokenizer", cfg["bm25"]["tokenizer"])
    
    def tok_fn(s: str):
        return re.findall(r"[a-z0-9]+", vnfold(s)) if tok == "vi_basic" else s.lower().split()

    q = tok_fn(args.query)
    scores = bm25.get_scores(q)
    order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
    s_ids = [metas[i]["doc_id"] for i in order]
    s_map = {metas[i]["doc_id"]: metas[i] | {"_sparse_score": float(scores[i])} for i in order}

    fused_ids = reciprocal_rank_fusion([d_ids, s_ids], k=rrf_k)[:top_k]

    for did in fused_ids:
        p = d_map.get(did) or s_map.get(did)
        dense = p.get("_dense_score", 0.0)
        sparse = p.get("_sparse_score", 0.0)
        preview = (p["text"][:80]).replace("\n", " ")
        print(f"{dense:.4f}\t{sparse:.4f}\t{p['doc_id']}\t{p['path_hierarchy']}\t{preview}…")


if __name__ == "__main__":
    main()
