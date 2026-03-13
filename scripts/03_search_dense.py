#!/usr/bin/env python
import argparse
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels
from sentence_transformers import SentenceTransformer
from scripts.utils import load_config



def main():
    parser = argparse.ArgumentParser(description="Dense vector search via Qdrant")
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--query", required=True)
    parser.add_argument("--top_k", type=int, default=None)

    args = parser.parse_args()
    cfg = load_config(args.config)

    model = SentenceTransformer(cfg["embedding"]["model_name"])
    qclient = QdrantClient(url=cfg["qdrant"]["url"], api_key=cfg["qdrant"].get("api_key") or None)

    qvec = model.encode([args.query], normalize_embeddings=cfg["embedding"].get("normalize", True))[0]

    hits = qclient.search(
        collection_name=cfg["qdrant"]["collection"],
        query_vector=qvec.tolist(),
        limit=args.top_k or cfg["search"]["top_k"],
        with_payload=True,
        with_vectors=False,
        score_threshold=None,
    )

    for h in hits:
        p = h.payload
        preview = (p["text"][:80]).replace("\n", " ")
        group    = p.get("group", "")
        doc_type = p.get("doc_type", "")
        src      = p.get("source_file", "")
        print(f"{h.score:.4f}\t{p['doc_id']}\t{group}\t{doc_type}\t{src}\t{p['path_hierarchy']}\t{preview}…")


if __name__ == "__main__":
    main()
