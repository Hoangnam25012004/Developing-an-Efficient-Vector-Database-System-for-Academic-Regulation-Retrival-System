"""Load the vector seed into Qdrant, so a fresh install serves the paper's vectors.

    python scripts/seed_qdrant.py               # create or top up the collection
    python scripts/seed_qdrant.py --if-missing  # docker entrypoint: no-op when it exists

The collection is created with the configuration in config.yaml, through the
same ensure_collection() the indexing pipeline uses (HNSW m=16,
ef_construct=100, full-scan and indexing thresholds of 10,000). Points are
built exactly as 02_embed_index builds them: id = int(chunk_id, 16) and
payload = the JSONL record. Vectors come from the seed written by
scripts/export_qdrant_seed.py; chunks the seed does not cover (documents
added after it was exported) are embedded with the configured model, so the
collection always matches the JSONL files.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import sys
import time
from pathlib import Path

import numpy as np
from qdrant_client.models import PointStruct

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import load_config  # noqa: E402

emb = importlib.import_module("src.pipeline.02_embed_index")

UPSERT_BATCH = 64


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def wait_for_qdrant(client, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            client.get_collections()
            return
        except Exception as exc:
            if time.monotonic() > deadline:
                raise SystemExit(f"Qdrant is not reachable after {timeout_s:.0f} s: {exc}")
            time.sleep(2)


def load_seed(seed_dir: Path, model: str, dim: int) -> dict[str, np.ndarray]:
    """chunk_id -> vector, or {} when there is no usable seed."""
    manifest_path = seed_dir / "manifest.json"
    if not manifest_path.exists():
        print(f"No seed at {seed_dir}: every chunk will be embedded.")
        return {}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("embedding_model") != model or manifest.get("dim") != dim:
        print(f"Seed was built with {manifest.get('embedding_model')} (dim {manifest.get('dim')}), "
              f"config uses {model} (dim {dim}): ignoring it.")
        return {}
    files = manifest["files"]
    vec_path = seed_dir / files["vectors"]["name"]
    ids_path = seed_dir / files["ids"]["name"]
    for path, meta in ((vec_path, files["vectors"]), (ids_path, files["ids"])):
        if sha256(path) != meta["sha256"]:
            raise SystemExit(f"{path} does not match the checksum in {manifest_path}")
    mat = np.load(vec_path)
    ids = json.loads(ids_path.read_text(encoding="utf-8"))
    if mat.shape != (len(ids), dim):
        raise SystemExit(f"seed shape {mat.shape} does not match {len(ids)} ids x {dim}")
    return {cid: mat[i] for i, cid in enumerate(ids)}


def main() -> int:
    ap = argparse.ArgumentParser(description="Load the vector seed into Qdrant")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--seed-dir", default="data/qdrant_seed")
    ap.add_argument("--if-missing", action="store_true",
                    help="do nothing when the collection already exists")
    ap.add_argument("--no-embed", action="store_true",
                    help="fail instead of embedding chunks the seed does not cover")
    ap.add_argument("--wait", type=float, default=180, help="seconds to wait for Qdrant")
    args = ap.parse_args()

    cfg = load_config(str(ROOT / args.config))
    vs = cfg["vector_store"]
    collection, dim = vs["collection_name"], int(vs["vector_size"])
    client = emb.get_qdrant_client(cfg)
    wait_for_qdrant(client, args.wait)

    if args.if_missing and client.collection_exists(collection):
        n = client.count(collection, exact=True).count
        print(f"Collection '{collection}' already exists ({n} points): nothing to do.")
        return 0

    records = list(emb.iter_chunks(ROOT / cfg["data"]["processed_dir"]))
    if not records:
        raise SystemExit("no chunks found: run the parsing pipeline first")
    seed = load_seed(ROOT / args.seed_dir, cfg["embedding"]["local_model"], dim)
    seeded = [r for r in records if r["chunk_id"] in seed]
    rest = [r for r in records if r["chunk_id"] not in seed]

    emb.ensure_collection(client, collection, dim, fresh=False, vs=vs)
    for batch in emb.batched(seeded, UPSERT_BATCH):
        points = [PointStruct(id=emb.chunk_id_to_point_id(r["chunk_id"]),
                              vector=seed[r["chunk_id"]].tolist(), payload=dict(r))
                  for r in batch]
        emb.with_retry(lambda: client.upsert(collection_name=collection, points=points, wait=True))
    if rest:
        if args.no_embed:
            raise SystemExit(f"{len(rest)} chunks are not in the seed (--no-embed)")
        print(f"Embedding {len(rest)} chunks the seed does not cover...")
        emb.upsert_records(client, collection, rest, emb.get_embedder(cfg))

    n = client.count(collection, exact=True).count
    if n != len(records):
        raise SystemExit(f"'{collection}' holds {n} points but there are {len(records)} chunks")
    print(f"Seeded '{collection}': {len(seeded)} vectors from the seed, "
          f"{len(rest)} embedded, {n} points in total.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
