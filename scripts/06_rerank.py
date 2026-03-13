#!/usr/bin/env python
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

"""
Cross-encoder reranking.

Two modes
---------
Interactive (single query):
    python scripts/06_rerank.py --query "Điều kiện miễn học phần là gì?"

Batch evaluation (runs over queries.jsonl, writes runs.jsonl for 07_eval_metrics.py):
    python scripts/06_rerank.py ^
        --queries data/eval/queries.jsonl ^
        --output  data/eval/runs_bge_m3.jsonl

Supported reranker models (set reranker.model_name in configs/default.yaml):
  • BAAI/bge-reranker-v2-m3                  → CrossEncoder (sentence-transformers)
  • BAAI/bge-reranker-v2-gemma               → FlagLLMReranker (FlagEmbedding)
  • BAAI/bge-reranker-v2-minicpm-layerwise   → LayerWiseFlagLLMReranker (FlagEmbedding)
  • jinaai/jina-reranker-v2-base-multilingual → CrossEncoder + trust_remote_code=True
  • itdainb/PhoRanker                        → CrossEncoder (sentence-transformers)

FlagEmbedding models require:  pip install FlagEmbedding
Jina model requires:           pip install einops
"""
import argparse
import pickle
from pathlib import Path
from typing import Any, List

from qdrant_client import QdrantClient
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer

from scripts.utils import load_config, read_jsonl, save_jsonl


# ── reranker factory ──────────────────────────────────────────────────────────

def load_reranker(model_name: str, use_fp16: bool = False) -> Any:
    """
    Auto-detect the correct reranker class from the model name.

    - *-minicpm-layerwise  → LayerWiseFlagLLMReranker  (FlagEmbedding)
    - *-gemma*             → FlagLLMReranker            (FlagEmbedding)
    - jina*                → CrossEncoder, trust_remote_code=True
    - everything else      → CrossEncoder               (sentence-transformers)
    """
    name_lower = model_name.lower()

    if "minicpm-layerwise" in name_lower:
        try:
            from FlagEmbedding import LayerWiseFlagLLMReranker  # type: ignore
        except ImportError:
            raise ImportError(
                "FlagEmbedding is required for the layerwise reranker.\n"
                "Install with:  pip install FlagEmbedding"
            )
        return LayerWiseFlagLLMReranker(model_name, use_fp16=use_fp16)

    if "gemma" in name_lower and "reranker" in name_lower:
        try:
            from FlagEmbedding import FlagLLMReranker  # type: ignore
        except ImportError:
            raise ImportError(
                "FlagEmbedding is required for the LLM-based reranker.\n"
                "Install with:  pip install FlagEmbedding"
            )
        return FlagLLMReranker(model_name, use_fp16=use_fp16)

    # Standard CrossEncoder (sentence-transformers)
    from sentence_transformers import CrossEncoder

    trust = "jina" in name_lower   # Jina requires trust_remote_code
    return CrossEncoder(model_name, trust_remote_code=trust)


def predict_scores(reranker: Any, pairs: List[tuple], batch_size: int,
                   cutoff_layers: List[int] | None = None) -> List[float]:
    """
    Unified predict interface for CrossEncoder and FlagEmbedding rerankers.

    Uses the full MRO (Method Resolution Order) for detection so that internal
    aliases like BaseLLMReranker are matched correctly regardless of which class
    name FlagEmbedding exposes publicly.

    cutoff_layers is only used by LayerWiseFlagLLMReranker.
    If not set, defaults to [28] (last layer = highest quality).
    """
    # Collect ALL class names in the hierarchy (leaf + every ancestor)
    # e.g. FlagLLMReranker() → actual instance is BaseLLMReranker,
    # so type().__name__ alone would miss it.
    all_cls_names = {c.__name__ for c in type(reranker).__mro__}

    # ── LayerWise (FlagEmbedding) ──────────────────────────────────────────────
    if any("LayerWise" in n for n in all_cls_names):
        layers = cutoff_layers or [28]
        raw = reranker.compute_score(pairs, cutoff_layers=layers, batch_size=batch_size)
        # returns list of lists (one per layer); take the last requested layer
        if raw and isinstance(raw[0], (list, tuple)):
            return [float(r[-1]) for r in raw]
        return [float(s) for s in raw]

    # ── Any FlagEmbedding LLM reranker ────────────────────────────────────────
    # FlagLLMReranker internally returns BaseLLMReranker (or similar aliases).
    # We match against all known FlagEmbedding base class names.
    _FLAG_LLM_NAMES = {
        "FlagLLMReranker", "BaseLLMReranker",
        "FlagReranker",    "AbsLLMReranker", "AbsReranker",
    }
    if all_cls_names & _FLAG_LLM_NAMES:
        return [float(s) for s in reranker.compute_score(pairs, batch_size=batch_size)]

    # ── CrossEncoder (sentence-transformers) ───────────────────────────────────
    import numpy as np
    raw = reranker.predict(pairs, batch_size=batch_size)
    return raw.tolist() if isinstance(raw, np.ndarray) else list(map(float, raw))


# ── retrieval helpers ─────────────────────────────────────────────────────────

def _tokenize(query: str, tokenizer_type: str) -> List[str]:
    import regex as re
    from scripts.utils import vnfold
    if tokenizer_type == "vi_basic":
        return re.findall(r"[a-z0-9]+", vnfold(query))
    return query.lower().split()


def collect_candidates(
    cfg: dict,
    query: str,
    top_k: int,
    encoder: SentenceTransformer,
    qclient: QdrantClient,
    bm25_obj: dict,
) -> List[dict]:
    """
    Union of dense (Qdrant) and sparse (BM25) top-k candidates.

    Accepts pre-initialised encoder/client/index so they are not
    reloaded on every call during batch evaluation.
    """
    # Dense
    qvec = encoder.encode(
        [query], normalize_embeddings=cfg["embedding"].get("normalize", True)
    )[0]
    dhits = qclient.search(
        collection_name=cfg["qdrant"]["collection"],
        query_vector=qvec.tolist(),
        limit=top_k,
        with_payload=True,
    )
    d_payloads = [h.payload for h in dhits]

    # Sparse
    bm25: BM25Okapi = bm25_obj["bm25"]
    metas: list     = bm25_obj["metas"]
    tok_type: str   = bm25_obj.get("tokenizer", cfg["bm25"]["tokenizer"])

    q_tok   = _tokenize(query, tok_type)
    scores  = bm25.get_scores(q_tok)
    order   = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
    s_payloads = [metas[i] for i in order]

    # Union by doc_id (dense takes precedence for payload)
    by_id: dict = {}
    for p in d_payloads + s_payloads:
        by_id[p["doc_id"]] = p
    return list(by_id.values())


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Cross-encoder reranking – single query or batch evaluation"
    )
    parser.add_argument("--config", default="configs/default.yaml")

    # Mode A: single interactive query
    parser.add_argument("--query",   default=None, help="Single query string")

    # Mode B: batch evaluation
    parser.add_argument("--queries", default=None, help="Path to queries.jsonl (batch mode)")
    parser.add_argument(
        "--output", default=None,
        help="Output path for runs.jsonl (required with --queries)"
    )

    # Shared
    parser.add_argument("--top_k",   type=int, default=20, help="Candidate pool per query")
    parser.add_argument("--final_k", type=int, default=10, help="Top-k after reranking")
    args = parser.parse_args()

    if args.queries is None and args.query is None:
        parser.error("Provide either --query (single) or --queries (batch)")
    if args.queries is not None and args.output is None:
        parser.error("--output is required when using --queries batch mode")

    cfg        = load_config(args.config)
    model_name = cfg["reranker"]["model_name"]
    batch_size = cfg["reranker"].get("batch_size", 32)
    use_fp16   = cfg["reranker"].get("use_fp16", False)
    cutoff_layers = cfg["reranker"].get("cutoff_layers", None)

    print(f"Loading reranker : {model_name}")
    reranker = load_reranker(model_name, use_fp16=use_fp16)

    # Pre-load shared resources once (not per-query)
    encoder = SentenceTransformer(cfg["embedding"]["model_name"])
    qclient = QdrantClient(
        url=cfg["qdrant"]["url"], api_key=cfg["qdrant"].get("api_key") or None
    )
    with open(Path(cfg["paths"]["bm25_index_path"]), "rb") as f:
        bm25_obj = pickle.load(f)

    # ── Batch evaluation mode ─────────────────────────────────────────────────
    if args.queries:
        queries     = read_jsonl(args.queries)
        all_results = []

        for q_item in queries:
            qid        = q_item["query_id"]
            query_text = q_item["query"]
            print(f"  [{qid}] {query_text[:70]}...")

            candidates = collect_candidates(
                cfg, query_text, args.top_k, encoder, qclient, bm25_obj
            )
            if not candidates:
                print(f"    → no candidates found, skipping.")
                continue

            pairs  = [(query_text, c["text"]) for c in candidates]
            scores = predict_scores(reranker, pairs, batch_size, cutoff_layers)

            order = sorted(
                range(len(scores)), key=lambda i: float(scores[i]), reverse=True
            )[: args.final_k]

            for rank, i in enumerate(order, start=1):
                all_results.append(
                    {
                        "query_id": qid,
                        "doc_id":   candidates[i]["doc_id"],
                        "rank":     rank,
                        "score":    round(float(scores[i]), 6),
                    }
                )

        save_jsonl(all_results, args.output)
        print(f"\nSaved {len(all_results)} result rows → {args.output}")
        return

    # ── Single-query interactive mode ─────────────────────────────────────────
    candidates = collect_candidates(
        cfg, args.query, args.top_k, encoder, qclient, bm25_obj
    )
    pairs  = [(args.query, c["text"]) for c in candidates]
    scores = predict_scores(reranker, pairs, batch_size, cutoff_layers)

    order = sorted(
        range(len(scores)), key=lambda i: float(scores[i]), reverse=True
    )[: args.final_k]

    for i in order:
        p        = candidates[i]
        preview  = p["text"][:100].replace("\n", " ")
        group    = p.get("group", "")
        doc_type = p.get("doc_type", "")
        src      = p.get("source_file", "")
        print(f"{float(scores[i]):.4f}\t{p['doc_id']}\t{group}\t{doc_type}\t{src}\t{p['path_hierarchy']}\t{preview}…")


if __name__ == "__main__":
    main()
