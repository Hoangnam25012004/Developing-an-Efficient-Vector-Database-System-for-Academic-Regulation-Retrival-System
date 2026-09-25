"""Embed the corpus and the evaluation queries once, cache to disk.

The vector-database experiments rebuild collections dozens of times across
the parameter sweep. Re-encoding 4k chunks on CPU for every configuration
would dominate the runtime and pollute the latency measurements, so the
encoder is run exactly once here and every later stage reads these arrays.

Outputs (eval/cache/):
  corpus_vectors.npy    float32 [N, dim]  L2-normalised
  corpus_payloads.json  list[dict]        chunk payloads, aligned with rows
  query_vectors.npy     float32 [Q, dim]
  query_meta.json       questions + ground-truth relevant_ids

Run from project root:  python eval/build_cache.py
"""

import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval._env import load_env  # noqa: E402

load_env()

from src.config import load_config  # noqa: E402

CACHE_DIR = ROOT / "eval" / "cache"


def iter_chunks(processed_dir: Path):
    for jsonl in sorted(processed_dir.glob("*.jsonl")):
        with jsonl.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    yield json.loads(line)


def main(config_path: str = "config.yaml") -> None:
    cfg = load_config(str(ROOT / config_path))
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    import torch
    from sentence_transformers import SentenceTransformer

    model_name = cfg["embedding"]["local_model"]
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Encoder: {model_name}  device={device}")
    model = SentenceTransformer(model_name, device=device)
    is_e5 = "e5" in model_name.lower()

    # ── Corpus ────────────────────────────────────────────────────────────
    processed_dir = Path(cfg["data"]["processed_dir"])
    if not processed_dir.is_absolute():
        processed_dir = ROOT / processed_dir
    chunks = list(iter_chunks(processed_dir))
    if not chunks:
        raise SystemExit(f"No chunks found in {processed_dir} — run 01_parse_chunk first.")
    print(f"Corpus: {len(chunks)} chunks")

    texts = [c["text"] for c in chunks]
    if is_e5:
        texts = [f"passage: {t}" for t in texts]

    t0 = time.perf_counter()
    corpus_vecs = model.encode(
        texts,
        batch_size=cfg["embedding"]["batch_size"],
        show_progress_bar=True,
        normalize_embeddings=True,
        convert_to_numpy=True,
    ).astype(np.float32)
    encode_s = time.perf_counter() - t0
    print(f"Encoded corpus in {encode_s:.1f}s ({len(chunks)/encode_s:.1f} chunks/s)")

    np.save(CACHE_DIR / "corpus_vectors.npy", corpus_vecs)
    (CACHE_DIR / "corpus_payloads.json").write_text(
        json.dumps(chunks, ensure_ascii=False), encoding="utf-8"
    )

    # ── Queries ───────────────────────────────────────────────────────────
    gt_path = Path(cfg["evaluation"]["test_queries_path"])
    if not gt_path.is_absolute():
        gt_path = ROOT / gt_path
    records = [
        json.loads(line)
        for line in gt_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    print(f"Queries: {len(records)}")

    questions = [r["question"] for r in records]
    q_texts = [f"query: {q}" for q in questions] if is_e5 else questions
    query_vecs = model.encode(
        q_texts, batch_size=32, normalize_embeddings=True, convert_to_numpy=True
    ).astype(np.float32)

    np.save(CACHE_DIR / "query_vectors.npy", query_vecs)
    (CACHE_DIR / "query_meta.json").write_text(
        json.dumps(
            [
                {
                    "id": r.get("id"),
                    "question": r["question"],
                    "answer": r.get("answer", ""),
                    "relevant_ids": r.get("relevant_ids", []),
                    "query_type": r.get("query_type", ""),
                }
                for r in records
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    meta = {
        "model": model_name,
        "device": device,
        "dim": int(corpus_vecs.shape[1]),
        "n_chunks": len(chunks),
        "n_queries": len(records),
        "corpus_encode_seconds": round(encode_s, 2),
    }
    (CACHE_DIR / "cache_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"\nCache written to {CACHE_DIR}")
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
