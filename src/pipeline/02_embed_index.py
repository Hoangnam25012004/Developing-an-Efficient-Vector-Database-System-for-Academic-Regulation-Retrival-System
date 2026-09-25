"""
Step 02 – Embed chunks and upsert to Qdrant vector store.

Reads:  data/processed/*.jsonl
Writes: Qdrant collection (local or Cloud)
"""

import argparse
import json
import os
import time
from pathlib import Path
from typing import Callable, Generator, Iterable

import yaml
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    MatchAny,
    PointIdsList,
    PointStruct,
    VectorParams,
)

from src.retrieval.qdrant_params import (
    describe,
    hnsw_config,
    optimizers_config,
    quantization_config,
)


def load_config(path: str = "config.yaml") -> dict:
    from src.config import load_config as _load
    return _load(path)


# ─── Embedding (local SentenceTransformer only) ────────────────────────────────

def get_embedder(cfg: dict):
    provider = cfg["embedding"]["provider"]
    if provider != "local":
        raise ValueError(
            f"Unknown embedding provider: {provider!r} (only 'local' is supported)"
        )

    import torch
    from sentence_transformers import SentenceTransformer
    model_name = cfg["embedding"]["local_model"]
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Embedding device: {device}")
    model = SentenceTransformer(model_name, device=device)
    is_e5 = "e5" in model_name.lower()

    def embed(texts: list[str]) -> list[list[float]]:
        # multilingual-e5-* requires "passage: " prefix for documents
        prefixed = [f"passage: {t}" if is_e5 else t for t in texts]
        return model.encode(
            prefixed, batch_size=32, show_progress_bar=False, normalize_embeddings=True
        ).tolist()

    return embed


# ─── Data loader ──────────────────────────────────────────────────────────────

def iter_chunks(processed_dir: Path) -> Generator[dict, None, None]:
    for jsonl in sorted(processed_dir.glob("*.jsonl")):
        with jsonl.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    yield json.loads(line)


def batched(iterable, n: int):
    batch = []
    for item in iterable:
        batch.append(item)
        if len(batch) == n:
            yield batch
            batch = []
    if batch:
        yield batch


# ─── Qdrant helpers ────────────────────────────────────────────────────────────

def get_qdrant_client(cfg: dict) -> QdrantClient:
    vs = cfg["vector_store"]
    api_key = vs.get("qdrant_api_key") or os.environ.get("QDRANT_API_KEY", "")
    url = vs["qdrant_url"]
    # timeout=300 prevents ReadTimeout on slow CPU embedding + large upsert batches
    if api_key:
        return QdrantClient(url=url, api_key=api_key, timeout=300)
    return QdrantClient(url=url, timeout=300)


def ensure_collection(
    client: QdrantClient,
    name: str,
    vector_size: int,
    *,
    fresh: bool = True,
    vs: dict | None = None,
):
    """Ensure the Qdrant collection exists with the right dimension.

    fresh=True (default): always recreate so stale points from a previous
        parse run with more chunks can't poison retrieval. Without this,
        point IDs are sequential ints starting at 0, so a re-embed with N
        chunks only overwrites IDs 0..N-1; IDs ≥ N from the prior run
        remain as zombie vectors that still get returned by similarity
        search.
    fresh=False: keep existing points (useful for incremental adds).
    """
    existing = {c.name for c in client.get_collections().collections}
    if name in existing:
        if fresh:
            print(f"Collection '{name}' exists — deleting for a fresh rebuild.")
            client.delete_collection(name)
            existing.discard(name)
        else:
            info = client.get_collection(name)
            existing_dim = info.config.params.vectors.size
            if existing_dim != vector_size:
                print(f"Collection '{name}' has dim={existing_dim}, expected {vector_size} — recreating.")
                client.delete_collection(name)
                existing.discard(name)
            else:
                print(f"Collection '{name}' already exists (dim={vector_size}) — upserting.")

    if name not in existing:
        vs = vs or {}
        client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
            hnsw_config=hnsw_config(vs),
            optimizers_config=optimizers_config(vs),
            quantization_config=quantization_config(vs),
        )
        print(f"Created collection '{name}' (dim={vector_size}) with {describe(vs)}")


# ─── ID derivation ─────────────────────────────────────────────────────────────

def chunk_id_to_point_id(chunk_id: str) -> int:
    """Convert chunk_id (16-char MD5 hex) to a stable 64-bit unsigned int.

    Why stable IDs: Qdrant upsert overwrites a point if its ID already exists.
    Using a deterministic int derived from chunk_id means re-running this
    pipeline (full or incremental) produces idempotent results — same chunk
    always lands at same point, no zombie vectors, no duplicates.
    """
    return int(chunk_id, 16)


# ─── Incremental operations (used by the ingestion worker) ────────────────────
#
# run() below rebuilds the collection from scratch. These functions keep the
# collection live and touch only the points of the documents being added,
# replaced or removed, so search keeps working while a document is indexed.
# Point ids derive from chunk_id (a content hash), so every operation here is
# idempotent and safe to re-run after a failure. No payload index is created:
# at this corpus size a filtered scan is cheap, and the collection keeps
# exactly the configuration the benchmarks were measured on.

def with_retry(fn: Callable, attempts: int = 3, delays: tuple = (2, 5, 10)):
    """Run a Qdrant call, retrying transient failures (Qdrant restarting, timeouts)."""
    for attempt in range(attempts):
        try:
            return fn()
        except Exception:
            if attempt == attempts - 1:
                raise
            time.sleep(delays[min(attempt, len(delays) - 1)])


def source_filter(sources: Iterable[str]) -> Filter:
    return Filter(must=[FieldCondition(key="source", match=MatchAny(any=list(sources)))])


def upsert_records(client: QdrantClient, collection: str, records: list[dict], embed,
                   embed_batch: int = 32, upsert_batch: int = 64,
                   progress: Callable[[int, int], None] | None = None) -> set[int]:
    """Embed and upsert chunk records; returns their point ids.

    Same vectors as run(): the same embedder, the same text, the same
    normalisation, and the same chunk_id → point id mapping.
    """
    ids: set[int] = set()
    buffer: list[PointStruct] = []
    done = 0
    for batch in batched(records, embed_batch):
        vectors = embed([r["text"] for r in batch])
        for rec, vec in zip(batch, vectors):
            pid = chunk_id_to_point_id(rec["chunk_id"])
            ids.add(pid)
            buffer.append(PointStruct(id=pid, vector=vec, payload=dict(rec)))
        while len(buffer) >= upsert_batch:
            send, buffer = buffer[:upsert_batch], buffer[upsert_batch:]
            with_retry(lambda: client.upsert(collection_name=collection, points=send, wait=True))
        done += len(batch)
        if progress:
            progress(done, len(records))
    if buffer:
        with_retry(lambda: client.upsert(collection_name=collection, points=buffer, wait=True))
    return ids


def _scroll_ids(client: QdrantClient, collection: str, flt: Filter | None) -> set[int]:
    ids: set[int] = set()
    offset = None
    while True:
        points, offset = with_retry(lambda: client.scroll(
            collection_name=collection, scroll_filter=flt, limit=1000, offset=offset,
            with_payload=False, with_vectors=False,
        ))
        ids.update(int(p.id) for p in points)
        if offset is None:
            return ids


def point_ids_for_sources(client: QdrantClient, collection: str, sources: Iterable[str]) -> set[int]:
    sources = list(sources)
    return _scroll_ids(client, collection, source_filter(sources)) if sources else set()


def all_point_ids(client: QdrantClient, collection: str) -> set[int]:
    return _scroll_ids(client, collection, None)


def delete_point_ids(client: QdrantClient, collection: str, ids: Iterable[int]) -> int:
    ids = sorted(ids)
    for start in range(0, len(ids), 1000):
        part = ids[start:start + 1000]
        with_retry(lambda: client.delete(
            collection_name=collection, points_selector=PointIdsList(points=part), wait=True,
        ))
    return len(ids)


def delete_sources(client: QdrantClient, collection: str, sources: Iterable[str]) -> None:
    sources = list(sources)
    if sources:
        with_retry(lambda: client.delete(
            collection_name=collection,
            points_selector=FilterSelector(filter=source_filter(sources)), wait=True,
        ))


def count_by_source(client: QdrantClient, collection: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    offset = None
    while True:
        points, offset = with_retry(lambda: client.scroll(
            collection_name=collection, limit=1000, offset=offset,
            with_payload=["source"], with_vectors=False,
        ))
        for p in points:
            src = (p.payload or {}).get("source") or "unknown"
            counts[src] = counts.get(src, 0) + 1
        if offset is None:
            return counts


def collection_vector_size(client: QdrantClient, collection: str) -> int | None:
    """Vector size of an existing collection, or None if it does not exist."""
    existing = {c.name for c in with_retry(lambda: client.get_collections()).collections}
    if collection not in existing:
        return None
    info = with_retry(lambda: client.get_collection(collection))
    return info.config.params.vectors.size


# ─── Main ──────────────────────────────────────────────────────────────────────

def run(config_path: str = "config.yaml", incremental: bool = False,
        only_files: list[str] | None = None):
    """Embed chunks and upsert to Qdrant.

    Args:
        config_path: path to config.yaml
        incremental: if True, do NOT delete existing collection — only add/update
                     points. Useful when adding a new PDF without re-embedding
                     the entire corpus.
        only_files:  optional list of JSONL filenames (basenames) to process.
                     If None → process all *.jsonl in processed_dir.
                     Useful with incremental=True to embed just the new file.
    """
    cfg = load_config(config_path)
    processed_dir = Path(cfg["data"]["processed_dir"])
    batch_size = cfg["embedding"]["batch_size"]
    collection = cfg["vector_store"]["collection_name"]
    vector_size = cfg["vector_store"]["vector_size"]

    embed = get_embedder(cfg)
    client = get_qdrant_client(cfg)
    # incremental=True → fresh=False (preserve existing points)
    ensure_collection(
        client, collection, vector_size, fresh=not incremental, vs=cfg["vector_store"]
    )

    embed_batch_size  = min(batch_size, 32)
    upsert_batch_size = min(batch_size, 64)

    total = 0
    point_buffer: list[PointStruct] = []

    # Build chunk iterator — optionally restricted to specific files
    if only_files:
        only_set = {Path(f).name for f in only_files}
        def filtered_chunks():
            for jsonl in sorted(processed_dir.glob("*.jsonl")):
                if jsonl.name not in only_set:
                    continue
                with jsonl.open("r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            yield json.loads(line)
        chunk_iter = filtered_chunks()
        print(f"Processing {len(only_set)} file(s): {sorted(only_set)}")
    else:
        chunk_iter = iter_chunks(processed_dir)

    for embed_batch in batched(chunk_iter, embed_batch_size):
        texts = [r["text"] for r in embed_batch]
        vectors = embed(texts)
        for rec, vec in zip(embed_batch, vectors):
            payload = dict(rec)
            # Stable ID derived from chunk_id (deterministic across runs)
            point_id = chunk_id_to_point_id(rec["chunk_id"])
            point_buffer.append(
                PointStruct(id=point_id, vector=vec, payload=payload)
            )
            total += 1

        while len(point_buffer) >= upsert_batch_size:
            batch_to_send = point_buffer[:upsert_batch_size]
            point_buffer   = point_buffer[upsert_batch_size:]
            client.upsert(collection_name=collection, points=batch_to_send)
            print(f"  Upserted {total} vectors…", end="\r")

    if point_buffer:
        client.upsert(collection_name=collection, points=point_buffer)
        print(f"  Upserted {total} vectors…", end="\r")

    mode = "incremental" if incremental else "full rebuild"
    print(f"\nDone ({mode}). Processed {total} vectors → collection '{collection}'.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Embed & index chunks to Qdrant")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument(
        "--incremental", action="store_true",
        help="Add/update points without deleting existing collection. "
             "Useful when uploading a new PDF without re-embedding the whole corpus.",
    )
    parser.add_argument(
        "--only-files", nargs="+", default=None, metavar="FILENAME",
        help="Process only these JSONL files (basenames). "
             "Combine with --incremental to embed just the new file(s).",
    )
    args = parser.parse_args()
    run(args.config, incremental=args.incremental, only_files=args.only_files)
