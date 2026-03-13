#!/usr/bin/env python
import argparse
import pickle
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple, Any

import regex as re
from qdrant_client import QdrantClient
from rank_bm25 import BM25Okapi
from sentence_transformers import CrossEncoder, SentenceTransformer

from scripts.utils import load_config, read_jsonl, vnfold


def write_jsonl(rows: List[dict], out_path: Path):
    import json
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def tok_vi_basic(text: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", vnfold(text))


def reciprocal_rank_fusion(rankings: List[List[str]], k: int = 60) -> List[Tuple[str, float]]:
    scores = defaultdict(float)
    for rlist in rankings:
        for rank, doc_id in enumerate(rlist):
            scores[doc_id] += 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


def get_qdrant(cfg) -> QdrantClient:
    return QdrantClient(url=cfg["qdrant"]["url"], api_key=cfg["qdrant"].get("api_key") or None)


def load_bm25(cfg) -> Tuple[BM25Okapi, List[dict], Any]:
    index_path = Path(cfg["paths"]["bm25_index_path"])
    if not index_path.exists():
        raise SystemExit(f"BM25 index not found: {index_path}. Run build first.")
    with open(index_path, "rb") as f:
        obj = pickle.load(f)
    bm25: BM25Okapi = obj["bm25"]
    metas: List[dict] = obj["metas"]
    tok = obj.get("tokenizer", cfg["bm25"]["tokenizer"])

    def tok_fn(s: str):
        return tok_vi_basic(s) if tok == "vi_basic" else s.lower().split()

    return bm25, metas, tok_fn


def dense_search(cfg, model: SentenceTransformer, client: QdrantClient, query: str, top_k: int):
    qvec = model.encode([query], normalize_embeddings=cfg["embedding"].get("normalize", True))[0]
    hits = client.search(
        collection_name=cfg["qdrant"]["collection"],
        query_vector=qvec.tolist(),
        limit=top_k,
        with_payload=True,
        with_vectors=False,
    )
    # doc_id, score, payload
    return [(h.payload["doc_id"], float(h.score), h.payload) for h in hits]


def sparse_search(bm25: BM25Okapi, metas: List[dict], tok_fn, query: str, top_k: int):
    q = tok_fn(query)
    scores = bm25.get_scores(q)
    order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
    return [(metas[i]["doc_id"], float(scores[i]), metas[i]) for i in order]


def rerank(cfg, reranker: CrossEncoder, query: str, payloads: Dict[str, dict], cand_ids: List[str], final_k: int):
    pairs = [(query, payloads[did]["text"]) for did in cand_ids if did in payloads]
    ids = [did for did in cand_ids if did in payloads]
    if not ids:
        return []
    scores = reranker.predict(pairs, batch_size=cfg["reranker"].get("batch_size", 32))
    order = sorted(range(len(scores)), key=lambda i: float(scores[i]), reverse=True)[:final_k]
    return [(ids[i], float(scores[i])) for i in order]


def preview(text: str, n: int = 220) -> str:
    t = (text or "").replace("\n", " ").strip()
    return t[:n] + ("…" if len(t) > n else "")


def main():
    ap = argparse.ArgumentParser(description="Build QA pooling candidates (Answer-focused groundtruth)")
    ap.add_argument("--config", default="configs/default.yaml")
    ap.add_argument("--questions", default="data/eval/qa_questions.jsonl")
    ap.add_argument("--out", default="data/eval/qa_pools.jsonl")

    ap.add_argument("--dense_k", type=int, default=50)
    ap.add_argument("--sparse_k", type=int, default=50)
    ap.add_argument("--rrf_k", type=int, default=60)

    ap.add_argument("--rerank_pool", type=int, default=100)
    ap.add_argument("--rerank_k", type=int, default=50)
    ap.add_argument("--max_candidates", type=int, default=200)

    args = ap.parse_args()
    cfg = load_config(args.config)

    questions = read_jsonl(Path(args.questions))

    qclient = get_qdrant(cfg)
    enc = SentenceTransformer(cfg["embedding"]["model_name"])
    bm25, metas, tok_fn = load_bm25(cfg)
    meta_by_id = {m["doc_id"]: m for m in metas}
    reranker = CrossEncoder(cfg["reranker"]["model_name"])

    out_rows = []

    for q in questions:
        qid = str(q["qid"])
        question = str(q["question"])
        answer_type = q.get("answer_type", "extractive")
        constraints = q.get("constraints", {})

        # dense
        dense_hits = dense_search(cfg, enc, qclient, question, args.dense_k)  # (doc_id, score, payload)
        # sparse
        sparse_hits = sparse_search(bm25, metas, tok_fn, question, args.sparse_k)  # (doc_id, score, payload)

        dense_ids = [did for did, _, _ in dense_hits]
        sparse_ids = [did for did, _, _ in sparse_hits]

        # rrf
        rrf_pairs = reciprocal_rank_fusion([dense_ids, sparse_ids], k=args.rrf_k)
        rrf_ids = [did for did, _ in rrf_pairs[: max(args.dense_k, args.sparse_k)]]

        # rerank pool = union of top rerank_pool from dense+sparse
        pool_ids = list(dict.fromkeys(dense_ids[: args.rerank_pool] + sparse_ids[: args.rerank_pool]))
        payloads = {}
        for did in pool_ids:
            if did in meta_by_id:
                payloads[did] = meta_by_id[did]
        rerank_pairs = rerank(cfg, reranker, question, payloads, pool_ids, args.rerank_k)

        # collect source info
        cand_map: Dict[str, dict] = {}

        def add(method: str, pairs: List[Tuple[str, float]], payload_lookup: Dict[str, dict]):
            for rank, (did, score) in enumerate(pairs, start=1):
                if did not in cand_map:
                    p = payload_lookup.get(did) or {}
                    cand_map[did] = {
                        "doc_id": did,
                        "sources": [],
                        "best_rank": rank,
                        "path_hierarchy": p.get("path_hierarchy"),
                        "page": p.get("page"),
                        "text_preview": preview(p.get("text", "")),
                    }
                cand_map[did]["sources"].append({"method": method, "rank": rank, "score": score})
                cand_map[did]["best_rank"] = min(cand_map[did]["best_rank"], rank)

        # build lookup from each method
        dense_lookup = {did: payload for did, _, payload in dense_hits}
        dense_pairs = [(did, score) for did, score, _ in dense_hits]
        add("dense", dense_pairs, dense_lookup)

        sparse_lookup = {did: payload for did, _, payload in sparse_hits}
        sparse_pairs = [(did, score) for did, score, _ in sparse_hits]
        add("sparse", sparse_pairs, sparse_lookup)

        add("rrf", rrf_pairs, {**dense_lookup, **sparse_lookup, **payloads})
        add("rerank", rerank_pairs, payloads)

        # sort & cap
        def sort_key(ent):
            max_score = max(s["score"] for s in ent["sources"])
            return (ent["best_rank"], -len(ent["sources"]), -max_score)

        candidates = sorted(cand_map.values(), key=sort_key)[: args.max_candidates]

        out_rows.append(
            {
                "qid": qid,
                "question": question,
                "answer_type": answer_type,
                "constraints": constraints,
                "candidates": candidates,
                "meta": {
                    "dense_k": args.dense_k,
                    "sparse_k": args.sparse_k,
                    "rrf_k": args.rrf_k,
                    "rerank_pool": args.rerank_pool,
                    "rerank_k": args.rerank_k,
                    "max_candidates": args.max_candidates,
                },
            }
        )

    write_jsonl(out_rows, Path(args.out))
    print(f"[ok] wrote qa pools: {args.out} (n={len(out_rows)})")


if __name__ == "__main__":
    main()
