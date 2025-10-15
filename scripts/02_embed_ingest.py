#!/usr/bin/env python
import argparse
from pathlib import Path
from typing import List
# thêm import ở đầu file
from uuid import uuid4

import numpy as np4
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels
from qdrant_client.models import PointStruct
from sentence_transformers import SentenceTransformer
from utils import load_config, read_jsonl


def get_client(cfg) -> QdrantClient:
    return QdrantClient(
        url=cfg["qdrant"]["url"], api_key=cfg["qdrant"].get("api_key") or None
    )


def ensure_collection(client: QdrantClient, cfg: dict, vec_size: int):
    coll = cfg["qdrant"]["collection"]
    try:
        client.get_collection(coll)
        return
    except Exception:
        pass
    print(f"[qdrant] creating collection: {coll}")
    client.recreate_collection(
        collection_name=coll,
        vectors_config=qmodels.VectorParams(
            size=vec_size,
            distance=qmodels.Distance.COSINE,
        ),
        hnsw_config=qmodels.HnswConfigDiff(
            m=cfg["qdrant"]["hnsw"]["m"],
            ef_construct=cfg["qdrant"]["hnsw"]["ef_construct"],
        ),
    )


def main():
    parser = argparse.ArgumentParser(
        description="Compute embeddings and ingest into Qdrant"
    )
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument(
        "--jsonl_glob", default=None, help="Override processed jsonl glob"
    )
    parser.add_argument("--batch_size", type=int, default=None)

    args = parser.parse_args()
    cfg = load_config(args.config)

    model_name = cfg["embedding"]["model_name"]
    model = SentenceTransformer(model_name)
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
        payloads = rows
        texts = [r["text"] for r in rows]
        print(f"[embed] {jf}  (n={len(texts)})")
        for i in range(0, len(texts), bs):
            batch = texts[i : i + bs]
            embs = model.encode(
                batch,
                normalize_embeddings=cfg["embedding"].get("normalize", True),
                show_progress_bar=False,
            )
            points = []
            for rec, vec in zip(payloads, embs):
                # id phải là int hoặc UUID; vector nên .tolist() đề phòng kiểu numpy
                points.append(
                    PointStruct(
                        id=str(uuid4()),
                        vector=vec.tolist(),
                        payload=rec,  # rec chứa doc_id, path_hierarchy, text, ...
                    )
                )
            client.upsert(collection_name=cfg["qdrant"]["collection"], points=points)
        print("[ok] upserted")


if __name__ == "__main__":
    main()
