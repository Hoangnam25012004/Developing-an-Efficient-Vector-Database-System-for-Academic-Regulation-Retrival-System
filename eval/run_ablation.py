"""Stage-wise retrieval ablation.

The existing evaluator scores `retrieved_ids` taken from the final answer's
sources — i.e. after reranking, deduplication, the max_per_source diversity
cap and the top_k truncation. Those numbers therefore describe the answer
composer, not the retriever, and cannot attribute a gain to the vector index,
BM25, the fusion step or the cross-encoder.

This runner evaluates each stage on its own output, over the same query set
and the same ground truth, so every row isolates one component:

  bm25          sparse only
  dense-exact   vector search, brute force  (index-independent quality ceiling)
  dense-hnsw    vector search, ANN graph    (what the index costs in quality)
  hybrid-rrf    RRF fusion, no reranking
  hybrid-rerank full pipeline (fusion + cross-encoder + diversity)

Run from project root:
  python eval/run_ablation.py
  python eval/run_ablation.py --stages bm25,dense-exact,hybrid-rerank
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval._env import load_env  # noqa: E402

load_env()

from src.config import load_config  # noqa: E402
from src.evaluation.metrics import bootstrap_ci, evaluate_retrieval  # noqa: E402
from src.retrieval.bm25_retriever import BM25Retriever  # noqa: E402
from src.retrieval.hybrid_retriever import reciprocal_rank_fusion  # noqa: E402
from src.retrieval.qdrant_params import search_params  # noqa: E402
from src.retrieval.reranker import Reranker  # noqa: E402
from src.retrieval.vector_store import VectorRetriever  # noqa: E402

CACHE_DIR = ROOT / "eval" / "cache"
RESULTS_DIR = ROOT / "eval" / "results"

ALL_STAGES = ["bm25", "dense-exact", "dense-hnsw", "hybrid-rrf", "hybrid-rerank"]


def paired_significance(
    hits_a: list[float], hits_b: list[float], name_a: str, name_b: str,
    n_resamples: int = 10000, seed: int = 42,
) -> dict:
    """McNemar's test plus a paired bootstrap CI for hits_b - hits_a.

    Both stages are scored on the same queries in the same order, so each
    query contributes one paired (right/wrong, right/wrong) observation —
    exactly the design McNemar's test is for, and the reason an unpaired
    comparison of two independent confidence intervals is the wrong tool:
    it ignores that both stages succeed or fail on many of the *same*
    queries, which is where the real information about a systematic gain
    (rather than one that could be sampling noise) lives.
    """
    from scipy.stats import binomtest

    n = len(hits_a)
    assert n == len(hits_b), "paired vectors must be the same length"

    both_correct = sum(1 for a, b in zip(hits_a, hits_b) if a == 1 and b == 1)
    both_wrong = sum(1 for a, b in zip(hits_a, hits_b) if a == 0 and b == 0)
    a_only = sum(1 for a, b in zip(hits_a, hits_b) if a == 1 and b == 0)
    b_only = sum(1 for a, b in zip(hits_a, hits_b) if a == 0 and b == 1)

    discordant = a_only + b_only
    # Exact binomial form of McNemar's test: under the null (no systematic
    # difference) each discordant pair is a coin flip as to which side wins.
    mcnemar_p = 1.0 if discordant == 0 else binomtest(
        min(a_only, b_only), discordant, 0.5, alternative="two-sided"
    ).pvalue

    rng = __import__("random").Random(seed)
    diffs = []
    idx = list(range(n))
    for _ in range(n_resamples):
        sample = [idx[rng.randrange(n)] for _ in range(n)]
        ma = sum(hits_a[i] for i in sample) / n
        mb = sum(hits_b[i] for i in sample) / n
        diffs.append(mb - ma)
    diffs.sort()
    lo = diffs[int(0.025 * n_resamples)]
    hi = diffs[int(0.975 * n_resamples) - 1]

    return {
        "name_a": name_a, "name_b": name_b, "n": n,
        "both_correct": both_correct, "both_wrong": both_wrong,
        "a_only": a_only, "b_only": b_only,
        "mcnemar_p": round(mcnemar_p, 6),
        "diff": round(sum(hits_b) / n - sum(hits_a) / n, 4),
        "diff_ci95": [round(lo, 4), round(hi, 4)],
    }


def load_queries(cfg: dict, gt_override: str | None = None) -> list[dict]:
    gt_path = Path(gt_override or cfg["evaluation"]["test_queries_path"])
    if not gt_path.is_absolute():
        gt_path = ROOT / gt_path
    return [
        json.loads(line)
        for line in gt_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _ids(docs: list[dict]) -> list[str]:
    return [d.get("chunk_id") or d.get("_id") for d in docs]


def main() -> None:
    ap = argparse.ArgumentParser(description="Stage-wise retrieval ablation")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--stages", default=",".join(ALL_STAGES))
    ap.add_argument("--top-k", type=int, default=10, help="cutoff for the reported ranking")
    ap.add_argument(
        "--pool", type=int, default=30, help="candidates drawn from each first-stage retriever"
    )
    ap.add_argument(
        "--gt",
        default=None,
        help="ground-truth JSONL override, e.g. the strict clause-level file "
        "produced by build_strict_gt.py (default: evaluation.test_queries_path)",
    )
    ap.add_argument("--tag", default="", help="label appended to the results filename")
    args = ap.parse_args()

    stages = [s.strip() for s in args.stages.split(",") if s.strip()]
    unknown = [s for s in stages if s not in ALL_STAGES]
    if unknown:
        raise SystemExit(f"Unknown stage(s): {unknown}. Choose from {ALL_STAGES}")

    cfg = load_config(str(ROOT / args.config))
    queries = load_queries(cfg, args.gt)
    scored = [q for q in queries if q.get("relevant_ids")]
    gt_label = args.gt or cfg["evaluation"]["test_queries_path"]
    n_labels = [len(q.get("relevant_ids", [])) for q in scored]
    print(f"Ground truth: {gt_label}")
    print(
        f"{len(queries)} queries loaded, {len(scored)} with ground truth, "
        f"{statistics.mean(n_labels):.1f} labels/query\n"
        if n_labels
        else f"{len(queries)} queries loaded, none with ground truth\n"
    )

    k_values = cfg["evaluation"]["k_values"]
    rrf_k = cfg["retrieval"]["rrf_k"]
    top_k_fusion = cfg["retrieval"]["top_k_fusion"]

    # Reuse cached query embeddings when available so the ablation does not
    # re-run the encoder and its cost never enters the per-stage timings.
    cached_qvecs = None
    if (CACHE_DIR / "query_vectors.npy").exists():
        qmeta = json.loads((CACHE_DIR / "query_meta.json").read_text(encoding="utf-8"))
        if [m["question"] for m in qmeta] == [q["question"] for q in queries]:
            cached_qvecs = np.load(CACHE_DIR / "query_vectors.npy")
            print("Using cached query embeddings.\n")

    need_dense = any(s != "bm25" for s in stages)
    need_sparse = any(s in ("bm25", "hybrid-rrf", "hybrid-rerank") for s in stages)

    dense = VectorRetriever(cfg) if need_dense else None
    sparse = BM25Retriever(cfg) if need_sparse else None
    reranker = Reranker(cfg) if "hybrid-rerank" in stages else None

    # Force the two dense conditions regardless of what config.yaml says, so
    # the comparison is explicit rather than inherited.
    vs = dict(cfg["vector_store"])
    exact_params = search_params({**vs, "search": {"hnsw_ef": None, "exact": True}})
    hnsw_params = search_params({**vs, "search": {"hnsw_ef": 128, "exact": False}})

    rows = []
    per_stage_hits: dict[str, list[float]] = {}
    per_query_hit1: dict[str, list[float]] = {}

    for stage in stages:
        print(f"── {stage} " + "─" * (50 - len(stage)))
        results, latencies = [], []

        for i, q in enumerate(queries):
            question = q["question"]
            qvec = cached_qvecs[i].tolist() if cached_qvecs is not None else None

            t0 = time.perf_counter()
            if stage == "bm25":
                docs = sparse.search(question, top_k=args.pool)
            elif stage in ("dense-exact", "dense-hnsw"):
                params = exact_params if stage == "dense-exact" else hnsw_params
                docs = dense.search(question, top_k=args.pool, vector=qvec, params=params)
            else:
                d = dense.search(question, top_k=args.pool, vector=qvec, params=hnsw_params)
                s = sparse.search(question, top_k=args.pool)
                docs = reciprocal_rank_fusion([d, s], id_key="_id", k=rrf_k)
                # Truncate exactly as HybridRetriever.search does. Without this
                # the reranker would see the full ~60-document union instead of
                # the 20 it sees in production, overstating both its cost and
                # its benefit.
                docs = docs[:top_k_fusion]
                if stage == "hybrid-rerank":
                    docs = reranker.rerank(question, docs)
            latencies.append((time.perf_counter() - t0) * 1000.0)

            results.append(
                {
                    "query": question,
                    "relevant_ids": q.get("relevant_ids", []),
                    "retrieved_ids": _ids(docs)[: args.top_k],
                }
            )

            if (i + 1) % 25 == 0:
                print(f"   {i+1}/{len(queries)}")

        graded = [r for r in results if r["relevant_ids"]]
        metrics = evaluate_retrieval(graded, k_values)

        lat = sorted(latencies)
        metrics["latency_mean_ms"] = round(statistics.mean(lat), 2)
        metrics["latency_p95_ms"] = round(lat[int(len(lat) * 0.95) - 1], 2)

        # Bootstrap CI on the headline metric so small-sample differences are
        # not over-read: 100 queries is not many.
        hits = [
            1.0 if any(rid in set(r["relevant_ids"]) for rid in r["retrieved_ids"][:5]) else 0.0
            for r in graded
        ]
        per_stage_hits[stage] = hits
        lo, hi = bootstrap_ci(hits)
        metrics["hit_rate@5_ci95"] = [lo, hi]

        # HR@1 per-query outcome, kept alongside HR@5's. HR@1 is the paper's
        # unbiased headline number (pool depth was symmetric only at rank 1),
        # so the paired significance test below needs its own hit vector
        # rather than reusing the HR@5 one computed for the CI above.
        # `queries` is loaded once and iterated in the same order for every
        # stage, and `graded` filters on a per-query criterion that doesn't
        # depend on the stage, so position i means the same query in every
        # stage's hit vector — no explicit id join needed to pair them later.
        per_query_hit1[stage] = [
            1.0 if r["retrieved_ids"] and r["retrieved_ids"][0] in set(r["relevant_ids"]) else 0.0
            for r in graded
        ]

        rows.append({"stage": stage, **metrics})
        print(
            f"   hit@5={metrics.get('hit_rate@5', 0):.4f} "
            f"[{lo:.3f}, {hi:.3f}]  "
            f"mrr@10={metrics.get('mrr@10', 0):.4f}  "
            f"ndcg@10={metrics.get('ndcg@10', 0):.4f}  "
            f"p50 lat={metrics['latency_mean_ms']:.1f}ms\n"
        )

    # ── Paired significance test: does reranking's HR@1 gain over BM25 ─────
    # survive sampling noise? Reviewer #2 asked for exactly this — the two
    # stages' HR@5 bootstrap CIs above are computed independently and can
    # overlap even when the paired, query-by-query difference is real (or
    # vice versa), so an unpaired comparison is the wrong tool here.
    significance = None
    if "bm25" in per_query_hit1 and "hybrid-rerank" in per_query_hit1:
        significance = paired_significance(
            per_query_hit1["bm25"], per_query_hit1["hybrid-rerank"],
            name_a="bm25", name_b="hybrid-rerank",
        )
        s = significance
        print("── Paired significance: bm25 vs hybrid-rerank (HR@1) " + "─" * 10)
        print(f"   n={s['n']}  both-correct={s['both_correct']}  "
              f"only-{s['name_b']}={s['b_only']}  only-{s['name_a']}={s['a_only']}  "
              f"both-wrong={s['both_wrong']}")
        print(f"   McNemar (exact binomial on discordant pairs): p={s['mcnemar_p']:.4f}")
        print(f"   Paired bootstrap 95% CI on the difference: "
              f"[{s['diff_ci95'][0]:+.3f}, {s['diff_ci95'][1]:+.3f}]  "
              f"(point estimate {s['diff']:+.3f})\n")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    suffix = f"_{args.tag}" if args.tag else ""
    out = RESULTS_DIR / f"ablation{suffix}_{ts}.json"
    out.write_text(
        json.dumps(
            {
                "timestamp": ts,
                "ground_truth": gt_label,
                "n_queries": len(queries),
                "n_scored": len(scored),
                "labels_per_query": round(statistics.mean(n_labels), 2) if n_labels else 0,
                "rows": rows,
                "significance_hr1_bm25_vs_hybrid_rerank": significance,
                # Raw per-query hit@1 vectors, so any other pair can be
                # re-tested later without rerunning the (slow) retrieval.
                "per_query_hit1": per_query_hit1,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print("=" * 78)
    print(f"{'stage':<16}{'hit@1':>8}{'hit@5':>8}{'hit@10':>8}{'mrr@10':>9}{'ndcg@10':>9}{'ms':>9}")
    print("-" * 78)
    for r in rows:
        print(
            f"{r['stage']:<16}"
            f"{r.get('hit_rate@1', 0):>8.4f}"
            f"{r.get('hit_rate@5', 0):>8.4f}"
            f"{r.get('hit_rate@10', 0):>8.4f}"
            f"{r.get('mrr@10', 0):>9.4f}"
            f"{r.get('ndcg@10', 0):>9.4f}"
            f"{r.get('latency_mean_ms', 0):>9.1f}"
        )
    print("=" * 78)
    print(f"\nSaved → {out}")


if __name__ == "__main__":
    main()
