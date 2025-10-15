#!/usr/bin/env python
"""
Evaluation utilities.

Expected inputs:
- queries.jsonl rows: {"query_id": "Q1", "query": "..."}
- qrels.jsonl rows:   {"query_id": "Q1", "relevant": ["A-12-3-a-0", "A-12-3-b-1", ...]}
- results.jsonl rows: {"query_id": "Q1", "doc_id": "...", "rank": 1}

Computes: Recall@k, MRR, nDCG@k
"""
import argparse
import math
from collections import defaultdict
from typing import Dict, List

from utils import read_jsonl


def recall_at_k(qrels, runs, k=10):
    hits = 0
    total = 0
    for q in qrels:
        qid = q["query_id"]
        rel = (
            set(q["relevant"])
            if isinstance(q["relevant"], list)
            else set(q["relevant"].keys())
        )
        recs = [r["doc_id"] for r in runs if r["query_id"] == qid and r["rank"] <= k]
        hits += len(set(recs) & rel)
        total += len(rel)
    return hits / max(1, total)


def mrr(runs, qrels):
    rel_map = {
        q["query_id"]: (
            set(q["relevant"])
            if isinstance(q["relevant"], list)
            else set(q["relevant"].keys())
        )
        for q in qrels
    }
    rr_sum, n = 0.0, 0
    for qid in rel_map:
        n += 1
        ranks = [
            r["rank"]
            for r in runs
            if r["query_id"] == qid and r["doc_id"] in rel_map[qid]
        ]
        rr_sum += 1.0 / min(ranks) if ranks else 0.0
    return rr_sum / max(1, n)


def ndcg_at_k(runs, qrels, k=10):
    rel_map = {
        q["query_id"]: (
            set(q["relevant"])
            if isinstance(q["relevant"], list)
            else set(q["relevant"].keys())
        )
        for q in qrels
    }
    total, n = 0.0, 0
    for qid, rels in rel_map.items():
        n += 1
        ranked = sorted(
            [r for r in runs if r["query_id"] == qid], key=lambda x: x["rank"]
        )[:k]
        dcg = 0.0
        for i, r in enumerate(ranked, start=1):
            gain = 1.0 if r["doc_id"] in rels else 0.0
            dcg += (2**gain - 1) / math.log2(i + 1)
        # ideal
        ideal_gains = [1.0] * min(len(rels), k)
        idcg = sum(
            (2**g - 1) / math.log2(i + 1) for i, g in enumerate(ideal_gains, start=1)
        )
        total += (dcg / idcg) if idcg > 0 else 0
    return total / max(1, n)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries", required=True)
    parser.add_argument("--qrels", required=True)
    parser.add_argument("--runs", required=True)
    parser.add_argument("--k", type=int, default=10)
    args = parser.parse_args()

    queries = read_jsonl(args.queries)
    qrels = read_jsonl(args.qrels)
    runs = read_jsonl(args.runs)

    print(f"Recall@{args.k}: {recall_at_k(qrels, runs, args.k):.4f}")
    print(f"MRR:          {mrr(runs, qrels):.4f}")
    print(f"nDCG@{args.k}: {ndcg_at_k(runs, qrels, args.k):.4f}")


if __name__ == "__main__":
    main()
