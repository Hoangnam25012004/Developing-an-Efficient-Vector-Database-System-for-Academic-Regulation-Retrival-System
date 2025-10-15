#!/usr/bin/env python
import argparse
import pickle
from pathlib import Path

from qdrant_client import QdrantClient
from rank_bm25 import BM25Okapi
from sentence_transformers import CrossEncoder, SentenceTransformer
from utils import load_config


def collect_candidates(cfg: dict, query: str, top_k: int):
    # dense
    enc = SentenceTransformer(cfg["embedding"]["model_name"])
    qvec = enc.encode(
        [query], normalize_embeddings=cfg["embedding"].get("normalize", True)
    )[0]
    qc = QdrantClient(
        url=cfg["qdrant"]["url"], api_key=cfg["qdrant"].get("api_key") or None
    )
    dhits = qc.search(
        collection_name=cfg["qdrant"]["collection"],
        query_vector=qvec.tolist(),
        limit=top_k,
        with_payload=True,
    )
    d_payloads = [h.payload for h in dhits]

    # sparse
    with open(Path(cfg["paths"]["bm25_index_path"]), "rb") as f:
        obj = pickle.load(f)
    bm25: BM25Okapi = obj["bm25"]
    metas = obj["metas"]
    tok = obj.get("tokenizer", cfg["bm25"]["tokenizer"])

    def tok_fn(s: str):
        import regex as re
        from utils import vnfold

        return (
            re.findall(r"[a-z0-9]+", vnfold(s))
            if tok == "vi_basic"
            else s.lower().split()
        )

    q = tok_fn(query)
    scores = bm25.get_scores(q)
    order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
    s_payloads = [metas[i] for i in order]

    # union by doc_id, preserve original payload
    by_id = {}
    for p in d_payloads + s_payloads:
        by_id[p["doc_id"]] = p
    return list(by_id.values())


def main():
    parser = argparse.ArgumentParser(
        description="Cross-encoder reranking on top-K candidates"
    )
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--query", required=True)
    parser.add_argument("--top_k", type=int, default=20)
    parser.add_argument("--final_k", type=int, default=10)

    args = parser.parse_args()
    cfg = load_config(args.config)

    candidates = collect_candidates(cfg, args.query, args.top_k)
    reranker = CrossEncoder(cfg["reranker"]["model_name"])  # BAAI/bge-reranker-v2-m3

    pairs = [(args.query, c["text"]) for c in candidates]
    scores = reranker.predict(pairs, batch_size=cfg["reranker"].get("batch_size", 32))

    order = sorted(range(len(scores)), key=lambda i: float(scores[i]), reverse=True)[
        : args.final_k
    ]
    for i in order:
        p = candidates[i]
        preview = (p["text"][:100]).replace("\n", " ")
        print(
            f"{float(scores[i]):.4f}\t{p['doc_id']}\t{p['path_hierarchy']}\t{preview}…"
        )


if __name__ == "__main__":
    main()
