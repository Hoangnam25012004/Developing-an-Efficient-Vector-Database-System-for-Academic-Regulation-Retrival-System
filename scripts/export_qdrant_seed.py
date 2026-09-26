"""Export the vectors of a live Qdrant collection as a seed file.

A fresh install (docker compose, CI, a reproduction run) loads the seed with
scripts/seed_qdrant.py and so serves exactly the vectors the KSE 2026
measurements were taken on, instead of re-embedding the corpus: that takes
about 14 minutes on CPU and is not bit-identical across machines.

Only points whose chunk_id belongs to the JSONL files in processed_dir are
exported; anything else in the collection (a document uploaded later) is
counted and left out. Payloads must equal the JSONL records.

    python scripts/export_qdrant_seed.py                    # config.yaml -> data/qdrant_seed/
    python scripts/export_qdrant_seed.py --out-dir /tmp/seed
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import load_config  # noqa: E402

emb = importlib.import_module("src.pipeline.02_embed_index")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description="Export a Qdrant collection as a vector seed")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--out-dir", default="data/qdrant_seed")
    args = ap.parse_args()

    cfg = load_config(str(ROOT / args.config))
    vs = cfg["vector_store"]
    collection = vs["collection_name"]
    records = list(emb.iter_chunks(ROOT / cfg["data"]["processed_dir"]))
    wanted = {emb.chunk_id_to_point_id(r["chunk_id"]): r for r in records}
    if len(wanted) != len(records):
        raise SystemExit("duplicate chunk_id in the JSONL files")

    client = emb.get_qdrant_client(cfg)
    vectors: dict[int, list[float]] = {}
    extra = mismatched = 0
    offset = None
    while True:
        points, offset = client.scroll(collection, limit=256, offset=offset,
                                       with_payload=True, with_vectors=True)
        for p in points:
            rec = wanted.get(p.id)
            if rec is None:
                extra += 1
                continue
            if p.payload != rec:
                mismatched += 1
            vectors[p.id] = p.vector
        if offset is None:
            break

    missing = [r["chunk_id"] for pid, r in wanted.items() if pid not in vectors]
    print(f"{collection}: {len(vectors)} of {len(records)} chunks found, "
          f"{extra} other points left out, {mismatched} payload mismatches")
    if missing or mismatched:
        raise SystemExit(f"cannot export: {len(missing)} chunks missing, "
                         f"{mismatched} payloads differ from the JSONL")

    out = ROOT / args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    ids = [r["chunk_id"] for r in records]
    mat = np.asarray([vectors[emb.chunk_id_to_point_id(c)] for c in ids], dtype=np.float32)
    vec_path = out / f"{collection}.f32.npy"
    ids_path = out / f"{collection}.ids.json"
    np.save(vec_path, mat)
    ids_path.write_text(json.dumps(ids), encoding="utf-8")

    lock = json.loads((ROOT / "models.lock.json").read_text(encoding="utf-8"))["models"]
    model = cfg["embedding"]["local_model"]
    try:
        server = client.info().version
    except Exception:
        server = "unknown"
    manifest = {
        "collection": collection,
        "count": len(ids),
        "dim": int(mat.shape[1]),
        "dtype": "float32",
        "order": "processed_dir/*.jsonl sorted by file name, then line order (as 02_embed_index reads them)",
        "embedding_model": model,
        "embedding_revision": lock.get(model),
        "exported_from": f"Qdrant {server}, collection '{collection}'",
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "files": {
            "vectors": {"name": vec_path.name, "sha256": sha256(vec_path)},
            "ids": {"name": ids_path.name, "sha256": sha256(ids_path)},
        },
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                                       encoding="utf-8")
    print(f"Wrote {len(ids)} x {mat.shape[1]} vectors to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
