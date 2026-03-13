#!/usr/bin/env python
import argparse
from pathlib import Path
from uuid import uuid4

from qdrant_client import QdrantClient
from qdrant_client.http.models import PointStruct, VectorParams, Distance, HnswConfigDiff
from sentence_transformers import SentenceTransformer
from scripts.utils import load_config, read_jsonl  

def get_client(cfg) -> QdrantClient:
    return QdrantClient(url=cfg["qdrant"]["url"], api_key=cfg["qdrant"].get("api_key") or None)

def ensure_collection(client, cfg, vec_size: int):
    coll = cfg["qdrant"]["collection"]
    print(f"[qdrant] recreating collection: {coll}")
    client.recreate_collection(
        collection_name=coll,
        vectors_config=VectorParams(size=vec_size, distance=Distance.COSINE),
        hnsw_config=HnswConfigDiff(
            m=cfg["qdrant"]["hnsw"]["m"],
            ef_construct=cfg["qdrant"]["hnsw"]["ef_construct"],
        ),
    )

def main():
    parser = argparse.ArgumentParser(description="Compute embeddings and ingest into Qdrant")
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--jsonl_glob", default=None)
    parser.add_argument("--batch_size", type=int, default=None)
    args = parser.parse_args()

    cfg = load_config(args.config)
    model = SentenceTransformer(cfg["embedding"]["model_name"])
    vec_size = model.get_sentence_embedding_dimension()

    client = get_client(cfg)
    ensure_collection(client, cfg, vec_size)

    glob_pat = args.jsonl_glob or cfg["paths"]["jsonl_glob"]
    files = list(sorted(Path().glob(glob_pat)))
    if not files:
        raise SystemExit(f"No JSONL files matched {glob_pat}")

    bs = args.batch_size or cfg["embedding"]["batch_size"]

    for jf in files:
        rows = read_jsonl(jf)
        texts = [r["text"] for r in rows]
        print(f"[embed] {jf}  (n={len(texts)})")
        for i in range(0, len(texts), bs):
            batch = texts[i:i+bs]
            embs = model.encode(
                batch,
                normalize_embeddings=cfg["embedding"].get("normalize", True),
                show_progress_bar=False,
            )
            points = []
            for rec, vec in zip(rows[i:i+bs], embs):
                points.append(
                    PointStruct(
                        id=str(rec.get("id") or uuid4()),
                        vector=vec.tolist(),
                        payload=rec,
                    )
                )
            client.upsert(collection_name=cfg["qdrant"]["collection"], points=points)
        print("[ok] upserted")

if __name__ == "__main__":
    main()
