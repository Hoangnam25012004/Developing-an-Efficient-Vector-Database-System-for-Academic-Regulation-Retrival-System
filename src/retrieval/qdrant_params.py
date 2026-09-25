"""Translate config.yaml vector_store settings into Qdrant model objects.

Kept separate from the retriever and the indexing script so that the
benchmark harness can build collections with sweep-generated overrides
without duplicating the mapping logic.
"""

from typing import Any

from qdrant_client.models import (
    BinaryQuantization,
    BinaryQuantizationConfig,
    HnswConfigDiff,
    OptimizersConfigDiff,
    QuantizationSearchParams,
    ScalarQuantization,
    ScalarQuantizationConfig,
    ScalarType,
    SearchParams,
)


def hnsw_config(vs: dict) -> HnswConfigDiff:
    h = vs.get("hnsw") or {}
    return HnswConfigDiff(
        m=h.get("m", 16),
        ef_construct=h.get("ef_construct", 100),
        full_scan_threshold=h.get("full_scan_threshold", 10000),
        on_disk=h.get("on_disk", False),
    )


def optimizers_config(vs: dict) -> OptimizersConfigDiff:
    """indexing_threshold governs when Qdrant actually builds the HNSW graph."""
    return OptimizersConfigDiff(indexing_threshold=vs.get("indexing_threshold", 10000))


def quantization_config(vs: dict):
    """Return a Qdrant quantization config, or None when disabled."""
    q = vs.get("quantization") or {}
    qtype = (q.get("type") or "none").lower()

    if qtype in ("none", "", None):
        return None
    if qtype == "scalar":
        return ScalarQuantization(
            scalar=ScalarQuantizationConfig(
                type=ScalarType.INT8,
                quantile=q.get("quantile", 0.99),
                always_ram=q.get("always_ram", True),
            )
        )
    if qtype == "binary":
        return BinaryQuantization(
            binary=BinaryQuantizationConfig(always_ram=q.get("always_ram", True))
        )
    raise ValueError(f"Unknown quantization type: {qtype!r} (use none|scalar|binary)")


def search_params(vs: dict) -> SearchParams | None:
    """Query-time knobs: HNSW beam width, exact bypass, quantization rescoring.

    Returns None when nothing is customised, so the client falls back to
    Qdrant's own defaults.
    """
    s = vs.get("search") or {}
    q = vs.get("quantization") or {}
    hnsw_ef = s.get("hnsw_ef")
    exact = s.get("exact", False)

    quant_params = None
    if (q.get("type") or "none").lower() != "none":
        quant_params = QuantizationSearchParams(
            ignore=False,
            rescore=q.get("rescore", True),
            oversampling=q.get("oversampling", 2.0),
        )

    if hnsw_ef is None and not exact and quant_params is None:
        return None

    return SearchParams(hnsw_ef=hnsw_ef, exact=exact, quantization=quant_params)


def describe(vs: dict) -> dict[str, Any]:
    """Flat, JSON-serialisable summary of the active configuration.

    Used to label rows in the benchmark result tables.
    """
    h = vs.get("hnsw") or {}
    s = vs.get("search") or {}
    q = vs.get("quantization") or {}
    return {
        "m": h.get("m", 16),
        "ef_construct": h.get("ef_construct", 100),
        "indexing_threshold": vs.get("indexing_threshold", 10000),
        "full_scan_threshold": h.get("full_scan_threshold", 10000),
        "hnsw_ef": s.get("hnsw_ef"),
        "exact": s.get("exact", False),
        "quantization": (q.get("type") or "none").lower(),
        "rescore": q.get("rescore", True),
        "oversampling": q.get("oversampling", 2.0),
    }
