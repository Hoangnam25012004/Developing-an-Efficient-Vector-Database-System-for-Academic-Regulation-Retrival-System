"""Reranker: local cross-encoder, with dedup + source diversity."""

from typing import Any

from sentence_transformers import CrossEncoder


# Maximum chars per chunk fed to the cross-encoder.
# 512 chars ≈ 130-150 Vietnamese tokens — keeps full Khoản visible while
# remaining within the model's 258-token context window after the query is added.
RERANK_MAX_CHARS = 512


def _dedup(docs: list[dict]) -> list[dict]:
    """Remove duplicates by chunk_id, then by text prefix."""
    seen_ids, seen_texts, out = set(), set(), []
    for d in docs:
        cid = d.get("chunk_id") or d.get("_id")
        text_key = d.get("text", "")[:80]
        if cid and cid in seen_ids:
            continue
        if text_key in seen_texts:
            continue
        if cid:
            seen_ids.add(cid)
        seen_texts.add(text_key)
        out.append(d)
    return out


def _diversify(scored: list[tuple[float, dict]], top_k: int, max_per_source: int) -> list[dict]:
    """Pick top_k docs while capping how many come from the same source file."""
    source_count: dict[str, int] = {}
    result = []
    # First pass: pick within cap
    for score, doc in scored:
        src = doc.get("source", "")
        if source_count.get(src, 0) < max_per_source:
            doc = dict(doc)
            doc["_score_rerank"] = float(score)
            result.append(doc)
            source_count[src] = source_count.get(src, 0) + 1
        if len(result) == top_k:
            return result
    # Second pass: fill remaining slots ignoring cap
    for score, doc in scored:
        if len(result) == top_k:
            break
        if doc not in [r for r in result]:
            doc = dict(doc)
            doc["_score_rerank"] = float(score)
            result.append(doc)
    return result


class Reranker:
    def __init__(self, cfg: dict):
        rr = cfg["reranking"]
        self.enabled = rr["enabled"]
        self.top_k = rr["top_k"]
        self.provider = rr["provider"]
        self.max_per_source = rr.get("max_per_source", 2)
        self.min_score = rr.get("min_score", 0.0)  # 0.0 = không lọc (hành vi cũ)

        if not self.enabled:
            return

        if self.provider != "cross-encoder":
            raise ValueError(
                f"Unknown reranker provider: {self.provider!r} (only 'cross-encoder' is supported)"
            )

        self._model = CrossEncoder(
            rr["cross_encoder_model"],
            trust_remote_code=True,
        )

    def rerank(self, query: str, docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not self.enabled or not docs:
            return _dedup(docs)[: self.top_k]

        docs = _dedup(docs)
        texts = [d["text"] for d in docs]

        # Truncate to RERANK_MAX_CHARS — was 200 (which suppressed legal
        # content past the article header). 512 lets the cross-encoder see
        # the full Khoản while staying inside its 258-token context.
        pairs = [[query, t[:RERANK_MAX_CHARS]] for t in texts]
        scores = self._model.predict(pairs).tolist()
        scored = sorted(zip(scores, docs), key=lambda x: x[0], reverse=True)

        # ── min_score filter ────────────────────────────────────────────────────
        # Loại bỏ các chunk có điểm rerank thấp hơn ngưỡng min_score.
        # Khi danh sách rỗng sau lọc, RAGChain.generate() sẽ trả về thông báo
        # "không tìm thấy thông tin" thay vì gửi context kém lên LLM.
        if self.min_score > 0.0:
            scored = [(s, d) for s, d in scored if s >= self.min_score]

        return _diversify(scored, self.top_k, self.max_per_source)
