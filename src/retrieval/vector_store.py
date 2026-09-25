"""Dense retriever using Qdrant + local SentenceTransformer."""

import os
import time
from typing import Any

from qdrant_client import QdrantClient
from sentence_transformers import SentenceTransformer

from .qdrant_params import search_params


class VectorRetriever:
    def __init__(self, cfg: dict):
        vs = cfg["vector_store"]
        api_key = vs.get("qdrant_api_key") or os.environ.get("QDRANT_API_KEY", "")
        url = vs["qdrant_url"]
        self.client = QdrantClient(url=url, api_key=api_key if api_key else None, timeout=60)
        self.collection = vs["collection_name"]
        self._search_params = search_params(vs)

        emb_cfg = cfg["embedding"]
        if emb_cfg["provider"] != "local":
            raise ValueError(
                f"Unknown embedding provider: {emb_cfg['provider']!r} (only 'local' is supported)"
            )
        self._local_model_name = emb_cfg["local_model"]
        self._st = SentenceTransformer(self._local_model_name)

        # Per-call timings (seconds) from the most recent search, so the
        # benchmark can separate embedding cost from vector-store cost.
        self.last_embed_s: float = 0.0
        self.last_search_s: float = 0.0

    def _embed(self, text: str) -> list[float]:
        # multilingual-e5-* models require "query: " prefix for queries
        if "e5" in self._local_model_name.lower():
            text = f"query: {text}"
        return self._st.encode([text], normalize_embeddings=True)[0].tolist()

    def search(
        self,
        query: str,
        top_k: int = 20,
        *,
        vector: list[float] | None = None,
        params: Any = None,
    ) -> list[dict[str, Any]]:
        """Dense search.

        vector: pre-computed query embedding — lets the benchmark embed once
                and reuse it across parameter sweeps, so the measured latency
                is the vector store's and not the encoder's.
        params: override the configured SearchParams for a single call.
        """
        t0 = time.perf_counter()
        if vector is None:
            vector = self._embed(query)
        t1 = time.perf_counter()

        response = self.client.query_points(
            collection_name=self.collection,
            query=vector,
            limit=top_k,
            with_payload=True,
            search_params=params if params is not None else self._search_params,
        )
        t2 = time.perf_counter()

        self.last_embed_s = t1 - t0
        self.last_search_s = t2 - t1

        results = []
        for hit in response.points:
            doc = dict(hit.payload)
            doc["_score_dense"] = hit.score
            # Use chunk_id from payload so RRF can deduplicate with BM25 results
            doc["_id"] = doc.get("chunk_id") or str(hit.id)
            results.append(doc)
        return results
