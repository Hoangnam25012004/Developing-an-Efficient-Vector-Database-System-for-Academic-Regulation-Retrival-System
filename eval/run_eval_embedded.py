"""
Self-contained evaluation harness using an EMBEDDED Qdrant instance.

Why this exists
---------------
The production pipeline talks to a Qdrant server over HTTP (Docker). When a
Docker daemon is not available, this harness runs the *identical* algorithm
(same embedding model, same HNSW cosine search, same RRF, same cross-encoder
rerank, same LLM-free tiering, same metrics) against an in-process embedded
Qdrant store. It does so by monkeypatching the two places that construct a
QdrantClient so they share a single embedded client:

  - src.pipeline.02_embed_index.get_qdrant_client   (index build)
  - src.retrieval.vector_store.QdrantClient          (query time)

Both v1 and v2 are evaluated through THIS SAME harness, so the comparison is
apples-to-apples: the only variable is the chunking corpus + ground truth.

Usage
-----
  python eval/run_eval_embedded.py --config config.yaml --gt eval/test_queries_gt_100_v1.jsonl
"""
import argparse
import importlib
import os
import sys
from pathlib import Path

# ── env + path bootstrap (mirror eval/run_eval.py) ──────────────────────────
ROOT = Path(__file__).resolve().parent.parent
env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()
sys.path.insert(0, str(ROOT))

from qdrant_client import QdrantClient


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--gt", required=True)
    ap.add_argument("--workers", type=int, default=1,
                    help="RAG workers. Keep 1: embedded local Qdrant is not "
                         "guaranteed thread-safe and we want determinism.")
    args = ap.parse_args()

    # One shared embedded client for both index build and query.
    shared = QdrantClient(location=":memory:")

    # ── Monkeypatch index-build client factory ──────────────────────────────
    embed_mod = importlib.import_module("src.pipeline.02_embed_index")
    embed_mod.get_qdrant_client = lambda cfg: shared

    # ── Monkeypatch query-time client constructor ───────────────────────────
    # VectorRetriever does `QdrantClient(url=..., api_key=..., timeout=...)`.
    # Replace the symbol in that module with a factory that ignores connection
    # kwargs and returns the shared embedded client.
    vs_mod = importlib.import_module("src.retrieval.vector_store")
    vs_mod.QdrantClient = lambda *a, **k: shared

    # ── Build the dense index into the embedded store ────────────────────────
    print(f"[embedded-eval] Building dense index from {args.config} …")
    embed_mod.run(args.config, incremental=False)

    # ── Run the full evaluation (retrieval + generation metrics) ────────────
    from src.evaluation.evaluator import run_evaluation
    print(f"[embedded-eval] Running evaluation  (gt={args.gt}) …")
    run_evaluation(args.config, rag_workers=args.workers, gt_path=args.gt)


if __name__ == "__main__":
    main()
