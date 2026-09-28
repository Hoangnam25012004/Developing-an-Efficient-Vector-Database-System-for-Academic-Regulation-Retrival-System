"""
End-to-end evaluator: runs test queries through the RAG pipeline
and computes retrieval + generation metrics against a ground-truth file.

Ground-truth file format (JSONL, one record per line):
  {
    "id":           int,
    "question":     str,
    "answer":       str,          # reference answer (human-written)
    "relevant_ids": list[str],    # ground-truth chunk IDs (empty → skip retrieval metrics)
    "sources":      list[{...}]   # metadata only
  }

Metrics computed:
  Retrieval  — Precision@k, Recall@k, F1@k, Hit Rate@k, MRR@k, MAP@k, NDCG@k
               (only for queries that have relevant_ids annotated)
  Generation — ROUGE-1 F1, ROUGE-L F1, BERTScore F1 (multilingual)
               (all queries, compared against reference answer)

Usage:
  python -m src.evaluation.evaluator
  python -m src.evaluation.evaluator --config config.yaml --workers 8
"""

import argparse
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from src.config import load_config
from src.chat.rag_chain import RAGChain
from src.evaluation.metrics import (
    evaluate_retrieval,
    evaluate_generation,
    evaluate_generation_per_query,
)


# ── Ground-truth loader ───────────────────────────────────────────────────────

def load_ground_truth(path: str) -> list[dict]:
    """Load JSONL or JSON ground-truth file."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Ground-truth file not found: {path}")

    records = []
    if p.suffix.lower() in (".jsonl",):
        with p.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    else:
        with p.open(encoding="utf-8") as f:
            records = json.load(f)

    return records


# ── RAG pipeline phase ────────────────────────────────────────────────────────

def _run_rag(args: tuple) -> dict:
    idx, question, ref_answer, relevant_ids, chain = args
    result = chain.query(question)
    return {
        "idx":           idx,
        "question":      question,
        "ref_answer":    ref_answer,
        "relevant_ids":  relevant_ids,
        "answer":        result["answer"],
        "sources":       result["sources"],
        "retrieved_ids": [s["chunk_id"] for s in result["sources"] if s.get("chunk_id")],
    }


def run_rag_phase(
    ground_truth: list[dict],
    chain: RAGChain,
    max_workers: int,
) -> list[dict]:
    tasks = [
        (i, r["question"], r.get("answer", ""), r.get("relevant_ids", []), chain)
        for i, r in enumerate(ground_truth)
    ]
    results = [None] * len(tasks)
    done, lock = 0, threading.Lock()

    print(f"Phase 1 - retrieval pipeline  ({len(tasks)} queries, {max_workers} workers)")
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_run_rag, t): t[0] for t in tasks}
        for fut in as_completed(futures):
            res = fut.result()
            results[res["idx"]] = res
            with lock:
                done += 1
                if done % 10 == 0 or done == len(tasks):
                    print(f"  [{done}/{len(tasks)}] queries done")

    return results


# ── Main ──────────────────────────────────────────────────────────────────────

def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def run_evaluation(
    config_path: str = "config.yaml",
    rag_workers: int = 8,
    gt_path: str | None = None,
) -> dict:
    cfg      = load_config(config_path)
    eval_cfg = cfg["evaluation"]
    k_values = eval_cfg["k_values"]
    output_dir = Path(eval_cfg["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)

    gt_file = gt_path or eval_cfg["test_queries_path"]
    ground_truth = load_ground_truth(gt_file)
    chain        = RAGChain(config_path)

    print(f"Evaluating {len(ground_truth)} queries\n" + "=" * 60)

    # ── Phase 1: run RAG pipeline ──────────────────────────────────────────
    rag_results = run_rag_phase(ground_truth, chain, rag_workers)

    # ── Phase 2: generation metrics (ROUGE + BERTScore) ───────────────────
    references  = [r["ref_answer"] for r in rag_results]
    hypotheses  = [r["answer"]     for r in rag_results]

    print(f"\nPhase 2 - Generation metrics  (ROUGE-1, ROUGE-L, BERTScore)")
    per_query_gen = evaluate_generation_per_query(references, hypotheses)
    gen_agg       = evaluate_generation(references, hypotheses)

    # ── Retrieval metrics (only queries with annotated relevant_ids) ───────
    # NOTE: retrieved_ids here come from the final answer's sources, i.e. AFTER
    # reranking, dedup, the max_per_source cap and top_k truncation. They score
    # the end-to-end pipeline, not the retriever. To attribute quality to an
    # individual stage (BM25 / dense / fusion / reranker), use eval/run_ablation.py,
    # which evaluates each stage on its own output.
    retrieval_inputs = [
        {
            "query":         r["question"],
            "relevant_ids":  r["relevant_ids"],    # ground truth
            "retrieved_ids": r["retrieved_ids"],   # system output
        }
        for r in rag_results
        if r["relevant_ids"]
    ]
    retrieval_agg = evaluate_retrieval(retrieval_inputs, k_values) if retrieval_inputs else {}

    # ── Build per-query output ─────────────────────────────────────────────
    per_query_results = []
    for res, gen in zip(rag_results, per_query_gen):
        per_query_results.append({
            "id":            res.get("idx", 0) + 1,
            "question":      res["question"],
            "ref_answer":    res["ref_answer"],
            "answer":        res["answer"],
            "sources":       res["sources"],
            "retrieved_ids": res["retrieved_ids"],
            "relevant_ids":  res["relevant_ids"],
            **gen,
        })

    summary = {
        "timestamp":          datetime.now().isoformat(),
        "n_queries":          len(ground_truth),
        "n_retrieval_scored": len(retrieval_inputs),
        "retrieval_metrics":  retrieval_agg,
        "generation_metrics": gen_agg,
    }

    # ── Save ──────────────────────────────────────────────────────────────
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    with (output_dir / f"summary_{ts}.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    with (output_dir / f"details_{ts}.json").open("w", encoding="utf-8") as f:
        json.dump(per_query_results, f, ensure_ascii=False, indent=2)

    # ── Print (ASCII-safe for Windows cp1252 consoles) ────────────────────
    sep = "=" * 60
    print(f"\n{sep}")
    print("EVALUATION SUMMARY")
    print(sep)

    if retrieval_agg:
        print(f"\n-- Retrieval Metrics  ({len(retrieval_inputs)} queries with relevant_ids) --")
        for metric, val in sorted(retrieval_agg.items()):
            print(f"  {metric:<20}: {val:.4f}")
    else:
        print("\n-- Retrieval Metrics  -- SKIPPED (no relevant_ids annotated) --")

    print("\n-- Generation Metrics --")
    primary = ["answer_recall", "bertscore_xlmr"]
    legacy  = ["rouge1", "rougeL", "bertscore_multi"]
    print("  [Primary - suited for verbatim (extractive) answers in Vietnamese]")
    for m in primary:
        if m in gen_agg:
            print(f"    {m:<25}: {gen_agg[m]:.4f}")
    print("  [Legacy - kept for comparison]")
    for m in legacy:
        if m in gen_agg:
            print(f"    {m:<25}: {gen_agg[m]:.4f}")

    print(f"\nResults saved to: {output_dir}")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate the retrieval pipeline")
    parser.add_argument("--config",  default="config.yaml")
    parser.add_argument("--workers", type=int, default=8,
                        help="Parallel workers for the retrieval pipeline (default: 8)")
    parser.add_argument("--gt", default=None,
                        help="Override ground-truth JSONL path (default: from config.yaml)")
    args = parser.parse_args()
    run_evaluation(args.config, args.workers, args.gt)
