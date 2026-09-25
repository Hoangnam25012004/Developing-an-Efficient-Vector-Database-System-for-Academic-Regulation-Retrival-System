"""Vector-database efficiency benchmark.

Measures what the deployed pipeline never measured: what the vector index
actually costs and what it gives up. Every experiment reuses the cached
embeddings from build_cache.py, so reported latency is the vector store's
own and never the sentence encoder's.

Experiments
  exact      Brute-force baseline. Produces the ANN ground truth and the
             latency floor/ceiling for this corpus size.
  ef         Sweep query-time hnsw_ef → recall/latency trade-off curve.
  build      Sweep construction parameters (m, ef_construct) → build time,
             index size, recall, latency.
  quant      none | scalar(int8) | binary → memory vs recall vs latency.
  scale      Replicate the corpus ×1…×N to locate the crossover point where
             HNSW actually starts beating brute force. This is the question
             that matters for institution-scale deployments.

Run from project root:
  python eval/bench_vectordb.py --experiment all
  python eval/bench_vectordb.py --experiment ef --repeats 5
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval._env import load_env  # noqa: E402

load_env()

from qdrant_client import QdrantClient  # noqa: E402
from qdrant_client.models import (  # noqa: E402
    Distance,
    PointStruct,
    VectorParams,
)

from src.config import load_config  # noqa: E402
from src.retrieval.qdrant_params import (  # noqa: E402
    hnsw_config,
    optimizers_config,
    quantization_config,
    search_params,
)

CACHE_DIR = ROOT / "eval" / "cache"
RESULTS_DIR = ROOT / "eval" / "results"
BENCH_COLLECTION = "bench_tmp"


# ══════════════════════════════════════════════════════════════════════════
# Cache loading
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class Cache:
    corpus: np.ndarray
    payloads: list[dict]
    queries: np.ndarray
    query_meta: list[dict]
    meta: dict


def load_cache() -> Cache:
    missing = [
        f
        for f in (
            "corpus_vectors.npy",
            "corpus_payloads.json",
            "query_vectors.npy",
            "query_meta.json",
        )
        if not (CACHE_DIR / f).exists()
    ]
    if missing:
        raise SystemExit(f"Cache incomplete ({missing}). Run: python eval/build_cache.py")
    return Cache(
        corpus=np.load(CACHE_DIR / "corpus_vectors.npy"),
        payloads=json.loads((CACHE_DIR / "corpus_payloads.json").read_text(encoding="utf-8")),
        queries=np.load(CACHE_DIR / "query_vectors.npy"),
        query_meta=json.loads((CACHE_DIR / "query_meta.json").read_text(encoding="utf-8")),
        meta=json.loads((CACHE_DIR / "cache_meta.json").read_text(encoding="utf-8"))
        if (CACHE_DIR / "cache_meta.json").exists()
        else {},
    )


# ══════════════════════════════════════════════════════════════════════════
# Qdrant helpers
# ══════════════════════════════════════════════════════════════════════════


def get_client(cfg: dict) -> QdrantClient:
    vs = cfg["vector_store"]
    import os

    api_key = vs.get("qdrant_api_key") or os.environ.get("QDRANT_API_KEY", "")
    return QdrantClient(url=vs["qdrant_url"], api_key=api_key or None, timeout=600)


def build_collection(
    client: QdrantClient,
    name: str,
    vectors: np.ndarray,
    payloads: list[dict],
    vs_override: dict,
    *,
    upsert_batch: int = 512,
    wait_for_index: bool = True,
) -> dict[str, Any]:
    """(Re)create a collection and load it. Returns build timings/sizes."""
    if client.collection_exists(name):
        client.delete_collection(name)

    dim = int(vectors.shape[1])
    client.create_collection(
        collection_name=name,
        vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        hnsw_config=hnsw_config(vs_override),
        optimizers_config=optimizers_config(vs_override),
        quantization_config=quantization_config(vs_override),
    )

    t0 = time.perf_counter()
    n = len(vectors)
    for start in range(0, n, upsert_batch):
        stop = min(start + upsert_batch, n)
        points = [
            PointStruct(id=i, vector=vectors[i].tolist(), payload=payloads[i])
            for i in range(start, stop)
        ]
        client.upsert(collection_name=name, points=points, wait=True)
    upsert_s = time.perf_counter() - t0

    index_s = 0.0
    if wait_for_index:
        index_s = wait_until_indexed(client, name)

    info = client.get_collection(name)
    return {
        "upsert_seconds": round(upsert_s, 3),
        "index_wait_seconds": round(index_s, 3),
        "build_seconds": round(upsert_s + index_s, 3),
        "points": info.points_count,
        "indexed_vectors": info.indexed_vectors_count,
        "segments": info.segments_count,
    }


def wait_until_indexed(client: QdrantClient, name: str, timeout_s: float = 900.0) -> float:
    """Block until Qdrant finishes optimizing, so timings exclude background work.

    Returns seconds waited. A collection whose indexing_threshold exceeds its
    point count never builds an HNSW graph; it still reaches status=green, so
    this returns promptly with indexed_vectors_count == 0.
    """
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout_s:
        info = client.get_collection(name)
        if str(info.status).lower().endswith("green"):
            # Give the optimizer a beat to publish counts, then confirm stability.
            time.sleep(0.3)
            again = client.get_collection(name)
            if str(again.status).lower().endswith("green"):
                return time.perf_counter() - t0
        time.sleep(0.5)
    return time.perf_counter() - t0


def collection_disk_bytes(container: str, name: str) -> int | None:
    """Measured on-disk footprint of one collection, via the Qdrant container.

    Returns None when the container is not reachable (e.g. Qdrant runs outside
    Docker), so the caller can fall back to reporting only in-memory estimates.
    """
    try:
        out = subprocess.run(
            ["docker", "exec", container, "du", "-sb", f"/qdrant/storage/collections/{name}"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if out.returncode != 0:
            return None
        return int(out.stdout.split()[0])
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════════════
# Search + metrics
# ══════════════════════════════════════════════════════════════════════════


def _params_payload(params) -> dict:
    """Serialise SearchParams for the raw REST call."""
    if params is None:
        return {}
    out: dict[str, Any] = {}
    if getattr(params, "hnsw_ef", None) is not None:
        out["hnsw_ef"] = params.hnsw_ef
    if getattr(params, "exact", None):
        out["exact"] = True
    q = getattr(params, "quantization", None)
    if q is not None:
        out["quantization"] = {
            "ignore": q.ignore,
            "rescore": q.rescore,
            "oversampling": q.oversampling,
        }
    return out


def run_queries(
    client: QdrantClient,
    name: str,
    query_vecs: np.ndarray,
    top_k: int,
    params,
    repeats: int = 3,
    warmup: int = 5,
    base_url: str = "http://localhost:6333",
) -> tuple[list[list[int]], dict[str, float]]:
    """Execute every query `repeats` times; return last-run IDs + latency stats.

    Two clocks are reported, because they answer different questions:
      client_*  wall time around the call — what a caller experiences, but at
                this corpus size it is dominated by HTTP round-trip.
      server_*  Qdrant's own `time` field — the search itself, which is the
                only figure that reflects the index.
    Reporting only the first would hide the index behind transport overhead.
    """
    import requests

    url = f"{base_url}/collections/{name}/points/query"
    extra = _params_payload(params)

    def one(vec) -> tuple[list[int], float, float]:
        body = {"query": vec.tolist(), "limit": top_k, "with_payload": False}
        if extra:
            body["params"] = extra
        t0 = time.perf_counter()
        resp = requests.post(url, json=body, timeout=120)
        client_ms = (time.perf_counter() - t0) * 1000.0
        resp.raise_for_status()
        data = resp.json()
        ids = [int(p["id"]) for p in data["result"]["points"]]
        return ids, client_ms, float(data.get("time", 0.0)) * 1000.0

    for i in range(min(warmup, len(query_vecs))):
        one(query_vecs[i])

    client_ms: list[float] = []
    server_ms: list[float] = []
    retrieved: list[list[int]] = []
    for _ in range(repeats):
        retrieved = []
        for vec in query_vecs:
            ids, c, s = one(vec)
            client_ms.append(c)
            server_ms.append(s)
            retrieved.append(ids)

    c_sorted = sorted(client_ms)
    s_sorted = sorted(server_ms)

    def pct(values: list[float], p: float) -> float:
        return round(values[max(int(len(values) * p) - 1, 0)], 4)

    return retrieved, {
        # server-side search time — the index's own cost
        "server_mean_ms": round(statistics.mean(s_sorted), 4),
        "server_p50_ms": pct(s_sorted, 0.50),
        "server_p95_ms": pct(s_sorted, 0.95),
        "server_p99_ms": pct(s_sorted, 0.99),
        "server_qps": round(1000.0 / statistics.mean(s_sorted), 1)
        if statistics.mean(s_sorted) > 0
        else 0.0,
        # end-to-end client latency, incl. HTTP round-trip
        "latency_mean_ms": round(statistics.mean(c_sorted), 3),
        "latency_p50_ms": pct(c_sorted, 0.50),
        "latency_p95_ms": pct(c_sorted, 0.95),
        "latency_p99_ms": pct(c_sorted, 0.99),
        "qps": round(1000.0 / statistics.mean(c_sorted), 2),
        "n_samples": len(c_sorted),
    }


def ann_recall(approx: list[list[int]], exact: list[list[int]], k: int) -> float:
    """Fraction of the true top-k that the approximate index returned.

    This is index fidelity, not task relevance — it asks only whether the ANN
    graph found what brute force would have found.
    """
    scores = []
    for a, e in zip(approx, exact):
        truth = set(e[:k])
        if not truth:
            continue
        scores.append(len(truth & set(a[:k])) / len(truth))
    return round(sum(scores) / len(scores), 4) if scores else 0.0


def task_metrics(
    retrieved: list[list[int]],
    payloads: list[dict],
    query_meta: list[dict],
    k_values: tuple[int, ...] = (1, 5, 10),
) -> dict[str, float]:
    """Retrieval quality against the human ground truth (chunk_id level)."""
    out: dict[str, float] = {}
    id_of = [p.get("chunk_id") for p in payloads]
    for k in k_values:
        hits, rrs = [], []
        for ids, meta in zip(retrieved, query_meta):
            relevant = set(meta.get("relevant_ids") or [])
            if not relevant:
                continue
            got = [id_of[i] if i < len(id_of) else None for i in ids[:k]]
            hits.append(1.0 if any(g in relevant for g in got) else 0.0)
            rr = 0.0
            for rank, g in enumerate(got, start=1):
                if g in relevant:
                    rr = 1.0 / rank
                    break
            rrs.append(rr)
        if hits:
            out[f"hit_rate@{k}"] = round(sum(hits) / len(hits), 4)
            out[f"mrr@{k}"] = round(sum(rrs) / len(rrs), 4)
    return out


# ══════════════════════════════════════════════════════════════════════════
# Configuration builder
# ══════════════════════════════════════════════════════════════════════════


def vs_config(
    *,
    m: int = 16,
    ef_construct: int = 100,
    indexing_threshold: int = 1,
    full_scan_threshold: int = 10,  # Qdrant rejects anything below 10
    hnsw_ef: int | None = None,
    exact: bool = False,
    quantization: str = "none",
    rescore: bool = True,
    oversampling: float = 2.0,
) -> dict:
    """Build a vector_store dict for one experimental condition.

    Defaults force the HNSW graph to actually exist (indexing_threshold=1,
    full_scan_threshold=0) — the opposite of the shipped configuration, where
    both thresholds sat above the corpus size and no graph was ever built.
    """
    return {
        "hnsw": {
            "m": m,
            "ef_construct": ef_construct,
            "full_scan_threshold": full_scan_threshold,
            "on_disk": False,
        },
        "indexing_threshold": indexing_threshold,
        "search": {"hnsw_ef": hnsw_ef, "exact": exact},
        "quantization": {
            "type": quantization,
            "always_ram": True,
            "quantile": 0.99,
            "rescore": rescore,
            "oversampling": oversampling,
        },
    }


@dataclass
class BenchContext:
    client: QdrantClient
    cache: Cache
    top_k: int
    repeats: int
    container: str
    base_url: str = "http://localhost:6333"
    rows: list[dict] = field(default_factory=list)

    def record(self, **row) -> None:
        self.rows.append(row)
        label = row.get("config", "")
        print(
            f"  {label:<30} "
            f"recall={row.get('ann_recall@10', float('nan')):.4f} "
            f"srv_p50={row.get('server_p50_ms', 0):.3f}ms "
            f"srv_p95={row.get('server_p95_ms', 0):.3f}ms "
            f"e2e_p50={row.get('latency_p50_ms', 0):.1f}ms "
            f"hit@5={row.get('hit_rate@5', 0):.4f} "
            f"disk={row.get('disk_mb', 0):.0f}MB"
        )


# ══════════════════════════════════════════════════════════════════════════
# Experiments
# ══════════════════════════════════════════════════════════════════════════


def measure(
    ctx: BenchContext,
    label: str,
    vs: dict,
    vectors: np.ndarray,
    payloads: list[dict],
    exact_ids: list[list[int]] | None,
    build_info: dict | None = None,
) -> tuple[list[list[int]], dict]:
    """Run one condition end-to-end and record a result row."""
    if build_info is None:
        build_info = build_collection(
            ctx.client, BENCH_COLLECTION, vectors, payloads, vs
        )
    ids, lat = run_queries(
        ctx.client,
        BENCH_COLLECTION,
        ctx.cache.queries,
        ctx.top_k,
        search_params(vs),
        repeats=ctx.repeats,
        base_url=ctx.base_url,
    )
    row: dict[str, Any] = {"config": label, **build_info, **lat}
    row.update(task_metrics(ids, payloads, ctx.cache.query_meta))
    if exact_ids is not None:
        for k in (1, 5, 10):
            row[f"ann_recall@{k}"] = ann_recall(ids, exact_ids, k)
    disk = collection_disk_bytes(ctx.container, BENCH_COLLECTION)
    if disk is not None:
        row["disk_bytes"] = disk
        row["disk_mb"] = round(disk / 1024 / 1024, 2)
    ctx.record(**row)
    return ids, row


def exp_exact(ctx: BenchContext) -> list[list[int]]:
    """Brute-force baseline — also the ground truth for ANN recall."""
    print("\n[exact] brute-force baseline")
    vs = vs_config(exact=True, indexing_threshold=10_000_000)
    ids, _ = measure(
        ctx, "exact (brute force)", vs, ctx.cache.corpus, ctx.cache.payloads, None
    )
    return ids


def exp_ef(ctx: BenchContext, exact_ids: list[list[int]]) -> None:
    """Query-time beam width sweep on a fixed graph."""
    print("\n[ef] hnsw_ef sweep (m=16, ef_construct=100)")
    vs_build = vs_config(m=16, ef_construct=100)
    build_info = build_collection(
        ctx.client, BENCH_COLLECTION, ctx.cache.corpus, ctx.cache.payloads, vs_build
    )
    for ef in (16, 32, 64, 128, 256, 512):
        vs = vs_config(m=16, ef_construct=100, hnsw_ef=ef)
        measure(
            ctx,
            f"hnsw ef={ef}",
            vs,
            ctx.cache.corpus,
            ctx.cache.payloads,
            exact_ids,
            build_info=dict(build_info),
        )


def exp_build(ctx: BenchContext, exact_ids: list[list[int]]) -> None:
    """Construction parameter sweep — build cost vs quality."""
    print("\n[build] m / ef_construct sweep")
    for m, efc in ((8, 100), (16, 100), (32, 100), (16, 200), (16, 400), (64, 200)):
        vs = vs_config(m=m, ef_construct=efc, hnsw_ef=128)
        measure(
            ctx,
            f"m={m} ef_construct={efc}",
            vs,
            ctx.cache.corpus,
            ctx.cache.payloads,
            exact_ids,
        )


def exp_quant(ctx: BenchContext, exact_ids: list[list[int]]) -> None:
    """Compression: memory saved vs recall lost."""
    print("\n[quant] quantization comparison")
    conditions = [
        ("float32 (none)", dict(quantization="none")),
        ("int8 scalar +rescore", dict(quantization="scalar", rescore=True)),
        ("int8 scalar -rescore", dict(quantization="scalar", rescore=False)),
        ("binary +rescore", dict(quantization="binary", rescore=True, oversampling=4.0)),
        ("binary -rescore", dict(quantization="binary", rescore=False)),
    ]
    for label, kwargs in conditions:
        vs = vs_config(m=16, ef_construct=100, hnsw_ef=128, **kwargs)
        measure(ctx, label, vs, ctx.cache.corpus, ctx.cache.payloads, exact_ids)


def _replicate(vectors: np.ndarray, payloads: list[dict], factor: int, rng: np.random.Generator):
    """Grow the corpus ×factor with small jitter, preserving chunk_id lineage.

    Exact duplicates would make ANN look artificially easy (identical vectors
    collapse in the graph), so each replica is perturbed and re-normalised.
    """
    if factor == 1:
        return vectors, payloads
    reps = [vectors]
    pays = list(payloads)
    for r in range(1, factor):
        noise = rng.normal(0.0, 0.01, size=vectors.shape).astype(np.float32)
        v = vectors + noise
        v /= np.linalg.norm(v, axis=1, keepdims=True)
        reps.append(v.astype(np.float32))
        for p in payloads:
            q = dict(p)
            q["chunk_id"] = f"{p.get('chunk_id')}__rep{r}"
            q["_replica"] = r
            pays.append(q)
    return np.vstack(reps), pays


def exp_scale(ctx: BenchContext, factors: tuple[int, ...]) -> None:
    """Locate the corpus size where HNSW overtakes brute force.

    At ~4k vectors the answer is not obvious: the graph traversal overhead can
    exceed a linear scan that fits comfortably in cache. Reporting the measured
    crossover is more useful to a deploying institution than asserting either.

    Read only latency and ANN recall from these rows. The task metrics
    (hit_rate/mrr) decline as the factor grows because each replica injects
    near-duplicate distractors that compete with the annotated chunk — an
    artifact of the growth method, not a property of the index. Quoting them
    as a quality result would be wrong.
    """
    print("\n[scale] brute force vs HNSW across corpus sizes")
    rng = np.random.default_rng(42)
    for factor in factors:
        vecs, pays = _replicate(ctx.cache.corpus, ctx.cache.payloads, factor, rng)
        n = len(vecs)
        print(f"\n  corpus ×{factor} = {n} vectors")

        vs_e = vs_config(exact=True, indexing_threshold=10_000_000)
        exact_ids, row_e = measure(ctx, f"N={n} exact", vs_e, vecs, pays, None)
        row_e["n_vectors"] = n
        row_e["scale_factor"] = factor

        vs_h = vs_config(m=16, ef_construct=100, hnsw_ef=128)
        _, row_h = measure(ctx, f"N={n} hnsw ef=128", vs_h, vecs, pays, exact_ids)
        row_h["n_vectors"] = n
        row_h["scale_factor"] = factor


# ══════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════


def main() -> None:
    ap = argparse.ArgumentParser(description="Vector database efficiency benchmark")
    ap.add_argument(
        "--experiment",
        default="all",
        choices=["all", "exact", "ef", "build", "quant", "scale"],
        help="which experiment to run",
    )
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--repeats", type=int, default=3, help="timed passes over the query set")
    ap.add_argument("--container", default="qdrant", help="Qdrant container name for du -sb")
    ap.add_argument(
        "--scale-factors",
        default="1,2,5,12,25",
        help="comma-separated corpus replication factors for --experiment scale",
    )
    ap.add_argument("--keep", action="store_true", help="keep the temporary bench collection")
    args = ap.parse_args()

    cfg = load_config(str(ROOT / args.config))
    cache = load_cache()
    client = get_client(cfg)

    print(f"Corpus {cache.corpus.shape} | queries {cache.queries.shape}")
    print(f"Encoder meta: {cache.meta}")

    ctx = BenchContext(
        client=client,
        cache=cache,
        top_k=args.top_k,
        repeats=args.repeats,
        container=args.container,
        base_url=cfg["vector_store"]["qdrant_url"].rstrip("/"),
    )

    which = args.experiment
    exact_ids: list[list[int]] | None = None

    if which in ("all", "exact", "ef", "build", "quant"):
        exact_ids = exp_exact(ctx)
    if which in ("all", "ef"):
        exp_ef(ctx, exact_ids)
    if which in ("all", "build"):
        exp_build(ctx, exact_ids)
    if which in ("all", "quant"):
        exp_quant(ctx, exact_ids)
    if which in ("all", "scale"):
        factors = tuple(int(x) for x in args.scale_factors.split(",") if x.strip())
        exp_scale(ctx, factors)

    if not args.keep and client.collection_exists(BENCH_COLLECTION):
        client.delete_collection(BENCH_COLLECTION)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    out = RESULTS_DIR / f"bench_{which}_{ts}.json"
    out.write_text(
        json.dumps(
            {
                "timestamp": ts,
                "experiment": which,
                "top_k": args.top_k,
                "repeats": args.repeats,
                "cache_meta": cache.meta,
                "rows": ctx.rows,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"\nSaved {len(ctx.rows)} rows → {out}")

    csv_out = out.with_suffix(".csv")
    if ctx.rows:
        keys: list[str] = []
        for r in ctx.rows:
            for k in r:
                if k not in keys:
                    keys.append(k)
        lines = [",".join(keys)]
        for r in ctx.rows:
            lines.append(",".join(str(r.get(k, "")) for k in keys))
        csv_out.write_text("\n".join(lines), encoding="utf-8")
        print(f"Saved CSV → {csv_out}")


if __name__ == "__main__":
    main()
