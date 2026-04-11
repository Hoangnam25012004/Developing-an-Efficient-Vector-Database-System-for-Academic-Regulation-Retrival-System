#!/usr/bin/env python
"""
Evaluation utilities for retrieval / reranking pipelines.

Expected inputs
---------------
queries.jsonl : {"query_id": "q001", "query": "..."}
qrels.jsonl   : {"query_id": "q001", "doc_id": "...", "relevance": 2}   ← TREC flat format
runs.jsonl    : {"query_id": "q001", "doc_id": "...", "rank": 1, "score": ...}

Relevance grades in qrels:
  2 = highly relevant   (counts as relevant in binary metrics)
  1 = relevant          (counts as relevant in binary metrics)
  0 = not relevant      (ignored)

Metrics computed at each specified @k (default: 5 and 10):
  Recall@k  Precision@k  MAP@k  nDCG@k (graded)  HitRate@k
Plus MRR (global, rank-unbounded).

Cross-model comparison workflow
--------------------------------
# 1. Edit configs/default.yaml → reranker.model_name: "<model>"
# 2. Generate reranked runs:
#    python scripts/06_rerank.py --queries data/eval/queries.jsonl --output data/eval/runs_<tag>.jsonl
# 3. Evaluate and record:
#    python scripts/07_eval_metrics.py ^
#        --queries data/eval/queries.jsonl ^
#        --qrels   data/eval/qrels.jsonl   ^
#        --runs    data/eval/runs_<tag>.jsonl ^
#        --model   "<model>" ^
#        --output  data/eval/comparison.csv
# 4. Repeat steps 1-3 for each model.  Open comparison.csv to pick the winner.
"""
import argparse
import csv
import math
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Set, Tuple

import sys
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
from scripts.utils import read_jsonl


# ── qrels helpers ─────────────────────────────────────────────────────────────

def _build_qrels_map(qrels_rows: List[dict]) -> Dict[str, Dict[str, int]]:
    """
    Convert flat TREC qrels rows into a nested dict:
        {query_id: {doc_id: relevance_grade}}

    Accepts both formats:
      - Flat TREC: {"query_id": ..., "doc_id": ..., "relevance": 2}
      - Grouped  : {"query_id": ..., "relevant": ["doc1", ...]}   (legacy)
    """
    qmap: Dict[str, Dict[str, int]] = defaultdict(dict)

    for row in qrels_rows:
        qid = row["query_id"]

        if "doc_id" in row:
            # TREC flat format
            grade = int(row.get("relevance", 1))
            qmap[qid][row["doc_id"]] = grade

        elif "relevant" in row:
            # Grouped legacy format – treat every entry as grade=1
            rel = row["relevant"]
            if isinstance(rel, list):
                for did in rel:
                    # Support both plain string IDs and {"doc_id": ..., ...} dicts
                    if isinstance(did, dict):
                        did = did["doc_id"]
                    qmap[qid][did] = 1
            else:  # dict {doc_id: grade}
                for did, grade in rel.items():
                    qmap[qid][did] = int(grade)

    return dict(qmap)


def _rel_set(qmap: Dict[str, Dict[str, int]], qid: str,
             min_grade: int = 1) -> Set[str]:
    """Return set of doc_ids with relevance >= min_grade for a given query."""
    return {did for did, g in qmap.get(qid, {}).items() if g >= min_grade}


# ── runs helper ───────────────────────────────────────────────────────────────

def _runs_by_qid(runs: List[dict]) -> Dict[str, List[dict]]:
    """Group runs by query_id, sorted by ascending rank."""
    by_q: Dict[str, List[dict]] = defaultdict(list)
    for r in runs:
        by_q[r["query_id"]].append(r)
    for qid in by_q:
        by_q[qid].sort(key=lambda x: x["rank"])
    return by_q


# ── metrics ───────────────────────────────────────────────────────────────────

def recall_at_k(qmap: dict, runs_by_q: dict, k: int,
                min_grade: int = 1) -> float:
    """Fraction of relevant docs retrieved in top-k (macro-averaged)."""
    hits, total = 0, 0
    for qid, doc_grades in qmap.items():
        rel = {did for did, g in doc_grades.items() if g >= min_grade}
        recs = {r["doc_id"] for r in runs_by_q.get(qid, []) if r["rank"] <= k}
        hits  += len(recs & rel)
        total += len(rel)
    return hits / max(1, total)


def precision_at_k(qmap: dict, runs_by_q: dict, k: int,
                   min_grade: int = 1) -> float:
    """Fraction of top-k results that are relevant (macro-averaged)."""
    prec_sum, n = 0.0, 0
    for qid, doc_grades in qmap.items():
        rel = {did for did, g in doc_grades.items() if g >= min_grade}
        top = [r["doc_id"] for r in runs_by_q.get(qid, [])[:k]]
        prec_sum += len(set(top) & rel) / k
        n += 1
    return prec_sum / max(1, n)


def mrr(qmap: dict, runs_by_q: dict, min_grade: int = 1) -> float:
    """Mean Reciprocal Rank – rank of first relevant result."""
    rr_sum, n = 0.0, 0
    for qid, doc_grades in qmap.items():
        rel = {did for did, g in doc_grades.items() if g >= min_grade}
        n += 1
        for r in runs_by_q.get(qid, []):
            if r["doc_id"] in rel:
                rr_sum += 1.0 / r["rank"]
                break
    return rr_sum / max(1, n)


def map_at_k(qmap: dict, runs_by_q: dict, k: int,
             min_grade: int = 1) -> float:
    """Mean Average Precision @k."""
    ap_sum, n = 0.0, 0
    for qid, doc_grades in qmap.items():
        rel = {did for did, g in doc_grades.items() if g >= min_grade}
        n += 1
        hits, ap = 0, 0.0
        for i, r in enumerate(runs_by_q.get(qid, [])[:k], start=1):
            if r["doc_id"] in rel:
                hits += 1
                ap += hits / i
        ap_sum += ap / max(1, len(rel))
    return ap_sum / max(1, n)


def ndcg_at_k(qmap: dict, runs_by_q: dict, k: int) -> float:
    """
    Normalised Discounted Cumulative Gain @k with graded relevance.
    gain = 2^grade - 1  (grade=2 → gain=3, grade=1 → gain=1, grade=0 → gain=0)
    """
    total, n = 0.0, 0
    for qid, doc_grades in qmap.items():
        n += 1
        ranked = runs_by_q.get(qid, [])[:k]

        dcg = sum(
            (2 ** doc_grades.get(r["doc_id"], 0) - 1) / math.log2(i + 1)
            for i, r in enumerate(ranked, start=1)
        )

        # ideal: sort all judged docs by grade descending, take top-k
        ideal_grades = sorted(doc_grades.values(), reverse=True)[:k]
        idcg = sum(
            (2 ** g - 1) / math.log2(i + 1)
            for i, g in enumerate(ideal_grades, start=1)
        )
        total += (dcg / idcg) if idcg > 0 else 0.0
    return total / max(1, n)


def hit_rate_at_k(qmap: dict, runs_by_q: dict, k: int,
                  min_grade: int = 1) -> float:
    """Fraction of queries with at least one relevant doc in top-k."""
    hits, n = 0, 0
    for qid, doc_grades in qmap.items():
        rel = {did for did, g in doc_grades.items() if g >= min_grade}
        n += 1
        recs = {r["doc_id"] for r in runs_by_q.get(qid, []) if r["rank"] <= k}
        if recs & rel:
            hits += 1
    return hits / max(1, n)


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Evaluate retrieval / reranking with multiple IR metrics"
    )
    parser.add_argument("--queries", required=True, help="queries.jsonl")
    parser.add_argument("--qrels",   required=True, help="qrels.jsonl (TREC flat format)")
    parser.add_argument("--runs",    required=True, help="runs.jsonl (must contain 'rank' field)")
    parser.add_argument(
        "--k", type=int, nargs="+", default=[5, 10],
        help="Cutoff(s) for @k metrics (default: 5 10)",
    )
    parser.add_argument(
        "--model", default="",
        help="Model name tag for CSV (e.g. 'BAAI/bge-reranker-v2-m3')",
    )
    parser.add_argument(
        "--output", default="",
        help="CSV file to append results for cross-model comparison",
    )
    args = parser.parse_args()

    qrels_rows = read_jsonl(args.qrels)
    runs       = read_jsonl(args.runs)

    qmap      = _build_qrels_map(qrels_rows)
    runs_by_q = _runs_by_qid(runs)

    model_tag        = args.model or args.runs
    query_ids_in_run = {r["query_id"] for r in runs}
    ks               = sorted(args.k)

    # ── compute metrics ───────────────────────────────────────────────────────
    all_metrics: Dict[str, float] = {}

    mrr_val = mrr(qmap, runs_by_q)
    all_metrics["MRR"] = mrr_val

    for k in ks:
        all_metrics[f"Recall@{k}"]    = recall_at_k(qmap, runs_by_q, k)
        all_metrics[f"Precision@{k}"] = precision_at_k(qmap, runs_by_q, k)
        all_metrics[f"MAP@{k}"]       = map_at_k(qmap, runs_by_q, k)
        all_metrics[f"nDCG@{k}"]      = ndcg_at_k(qmap, runs_by_q, k)
        all_metrics[f"HitRate@{k}"]   = hit_rate_at_k(qmap, runs_by_q, k)

    # ── print report ──────────────────────────────────────────────────────────
    sep = "=" * 62
    print(f"\n{sep}")
    print(f"  Model  : {model_tag}")
    print(f"  Runs   : {args.runs}")
    print(f"  Queries: {len(query_ids_in_run)} in run / {len(qmap)} in qrels")
    print(sep)
    print(f"  {'Metric':<20}  {'Value':>8}")
    print(f"  {'-'*28}")
    print(f"  {'MRR':<20}  {mrr_val:>8.4f}")
    for k in ks:
        print(f"  {'─'*28}")
        for name in (
            f"Recall@{k}", f"Precision@{k}",
            f"MAP@{k}", f"nDCG@{k}", f"HitRate@{k}",
        ):
            print(f"  {name:<20}  {all_metrics[name]:>8.4f}")
    print(f"{sep}\n")

    # ── append to CSV ─────────────────────────────────────────────────────────
    if args.output:
        out_path  = Path(args.output)
        write_hdr = not out_path.exists()
        cols = ["model", "runs", "MRR"] + [
            f"{m}@{k}"
            for k in ks
            for m in ("Recall", "Precision", "MAP", "nDCG", "HitRate")
        ]
        with open(out_path, "a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=cols)
            if write_hdr:
                writer.writeheader()
            row: Dict[str, str] = {"model": model_tag, "runs": args.runs}
            row.update({metric: f"{val:.4f}" for metric, val in all_metrics.items()})
            writer.writerow(row)
        print(f"  Results appended → {out_path}\n")


if __name__ == "__main__":
    main()
