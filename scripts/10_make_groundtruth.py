#!/usr/bin/env python
"""
10_make_groundtruth.py
======================
Converts data/Q&A/Q&A.JSONL (350 Q&A pairs) into the two evaluation files
that 07_eval_metrics.py needs:

    data/eval/queries.jsonl   — {query_id, query}
    data/eval/qrels.jsonl     — {query_id, doc_id, relevance}

Resolution strategy (no source_file field in Qdrant chunks):
  1. Exact match  — evidence text appears verbatim inside chunk text  → relevance 2
  2. Word overlap — normalised word-level Jaccard over evidence+answer  → relevance 2
                    if score >= --min_sim, else written to unresolved
  3. Unresolved   — written to data/eval/unresolved.jsonl for manual review

Usage (from project root):
    python scripts/10_make_groundtruth.py
    python scripts/10_make_groundtruth.py --dry_run
    python scripts/10_make_groundtruth.py --min_sim 0.6 --out_dir data/eval/full
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from qdrant_client import QdrantClient

# ── import utils the same way every other script in this folder does ──────────
sys.path.insert(0, str(Path(__file__).parent))
from utils import load_config, vnfold


# ── text normalisation ────────────────────────────────────────────────────────

def _norm(text: str) -> str:
    """Fold diacritics, lowercase, collapse whitespace."""
    return re.sub(r"\s+", " ", vnfold(text or "")).strip()


def _words(text: str) -> set:
    return set(re.findall(r"[a-z0-9]+", _norm(text)))


# ── matching ──────────────────────────────────────────────────────────────────

def score_chunk(evidence: str, answer: str, chunk_text: str) -> Tuple[float, bool]:
    """
    Returns (score, is_exact_match).

    Strategy 1 – Exact substring:
        Normalised evidence appears verbatim inside normalised chunk → score=1.0.
        Most reliable signal; used for form codes, article refs, etc.

    Strategy 2 – Evidence recall:
        score = |evidence_words ∩ chunk_words| / |evidence_words|
        Measures "what fraction of evidence words are present in the chunk?"
        Using RECALL (not Jaccard) is correct here because evidence is short
        and chunks are long — Jaccard would always be near zero.

    Strategy 3 – Answer recall (fallback for very short evidence < 4 words):
        Re-score using the answer text when evidence alone is too vague.
    """
    norm_ev    = _norm(evidence)
    norm_chunk = _norm(chunk_text)

    # ── Strategy 1: exact substring ───────────────────────────────────────────
    if norm_ev and norm_ev in norm_chunk:
        return 1.0, True

    chunk_words = _words(chunk_text)

    # ── Strategy 2: evidence recall ───────────────────────────────────────────
    ev_words = _words(evidence)
    if ev_words:
        ev_recall = len(ev_words & chunk_words) / len(ev_words)
        # If evidence is long enough (≥4 words), trust recall alone
        if len(ev_words) >= 4:
            return ev_recall, False
    else:
        ev_recall = 0.0

    # ── Strategy 3: answer recall fallback (evidence is short or empty) ───────
    ans_words = _words(answer)
    if ans_words:
        ans_recall = len(ans_words & chunk_words) / len(ans_words)
        # Combine: evidence recall weighted 60 %, answer recall 40 %
        combined = 0.6 * ev_recall + 0.4 * ans_recall
        return combined, False

    return ev_recall, False


# ── Qdrant chunk loader ───────────────────────────────────────────────────────

def load_all_chunks(cfg: dict) -> List[dict]:
    qc = QdrantClient(
        url=cfg["qdrant"]["url"],
        api_key=cfg["qdrant"].get("api_key") or None,
    )
    collection = cfg["qdrant"]["collection"]
    all_chunks: List[dict] = []
    offset = None

    print("  Loading all chunks from Qdrant …", end="", flush=True)
    while True:
        result, next_offset = qc.scroll(
            collection_name=collection,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for point in result:
            all_chunks.append(point.payload)
        if next_offset is None:
            break
        offset = next_offset

    print(f" {len(all_chunks)} chunks loaded.")
    return all_chunks


# ── Resolve one Q&A record ────────────────────────────────────────────────────

def resolve(
    evidence: str,
    answer: str,
    all_chunks: List[dict],
    min_sim: float,
) -> Tuple[Optional[str], float, bool]:
    """
    Returns (doc_id | None, best_score, is_exact).
    doc_id is None only when no chunks exist at all.
    Caller decides what to do with the score.
    """
    best_id, best_score, best_exact = None, -1.0, False

    for chunk in all_chunks:
        s, exact = score_chunk(evidence, answer, chunk.get("text", ""))
        if s > best_score:
            best_score, best_id, best_exact = s, chunk["doc_id"], exact

    return best_id, best_score, best_exact


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Build queries.jsonl + qrels.jsonl ground truth from Q&A.JSONL"
    )
    parser.add_argument("--config",  default="configs/default.yaml")
    parser.add_argument(
        "--qa_file", default="data/Q&A/Q&A.JSONL",
        help="Path to Q&A JSONL (default: data/Q&A/Q&A.JSONL)",
    )
    parser.add_argument(
        "--out_dir", default="data/eval",
        help="Output directory for queries.jsonl / qrels.jsonl / unresolved.jsonl",
    )
    parser.add_argument(
        "--min_sim", type=float, default=0.60,
        help="Min recall score to accept as resolved (0–1, default 0.60)",
    )
    parser.add_argument(
        "--dry_run", action="store_true",
        help="Show stats without writing any files",
    )
    args = parser.parse_args()

    cfg     = load_config(args.config)
    qa_path = Path(args.qa_file)
    out_dir = Path(args.out_dir)

    if not qa_path.exists():
        sys.exit(f"ERROR: QA file not found: {qa_path}")

    # ── Load Q&A records ──────────────────────────────────────────────────────
    records = [json.loads(line) for line in qa_path.open(encoding="utf-8") if line.strip()]
    print(f"\nLoaded {len(records)} Q&A records from {qa_path}")

    # ── Load all Qdrant chunks ────────────────────────────────────────────────
    all_chunks = load_all_chunks(cfg)

    # ── Resolve ───────────────────────────────────────────────────────────────
    queries_rows:    List[dict] = []
    qrels_rows:      List[dict] = []
    unresolved_rows: List[dict] = []

    exact_count = overlap_count = low_conf_count = 0

    for record in records:
        raw_id  = record["id"]
        qid     = f"q{raw_id:03d}"
        question = record["question"]
        answer   = record.get("answer", "")
        src      = record.get("source", {})
        evidence = src.get("evidence", "")

        queries_rows.append({"query_id": qid, "query": question})

        doc_id, score, is_exact = resolve(evidence, answer, all_chunks, args.min_sim)

        if doc_id is None:
            unresolved_rows.append({
                "query_id": qid, "question": question,
                "evidence": evidence, "reason": "no chunks in index",
            })
            continue

        if is_exact or score >= args.min_sim:
            qrels_rows.append({"query_id": qid, "doc_id": doc_id, "relevance": 2})
            if is_exact:
                exact_count += 1
            else:
                overlap_count += 1
        else:
            # Low confidence – send to manual review with the best guess
            unresolved_rows.append({
                "query_id":    qid,
                "question":    question,
                "evidence":    evidence,
                "answer":      answer,
                "best_doc_id": doc_id,
                "overlap":     round(score, 4),
                "reason":      f"low overlap ({score:.2f} < {args.min_sim})",
            })
            low_conf_count += 1

    # ── Print summary ─────────────────────────────────────────────────────────
    total     = len(records)
    resolved  = exact_count + overlap_count
    sep = "=" * 58
    print(f"\n{sep}")
    print(f"  Ground-truth resolution summary ({total} Q&A records)")
    print(sep)
    print(f"  ✅  Exact match  → qrels (relevance=2) : {exact_count:>4}  ({100*exact_count/total:.1f}%)")
    print(f"  ✅  Word overlap → qrels (relevance=2) : {overlap_count:>4}  ({100*overlap_count/total:.1f}%)")
    print(f"  ⚠️   Low confidence → unresolved        : {low_conf_count:>4}  ({100*low_conf_count/total:.1f}%)")
    print(sep)
    print(f"  queries.jsonl    : {len(queries_rows)} rows")
    print(f"  qrels.jsonl      : {len(qrels_rows)} rows")
    if unresolved_rows:
        print(f"  unresolved.jsonl : {len(unresolved_rows)} rows  ← review manually")
    print(sep)

    if args.dry_run:
        print("\n  [dry_run] No files written.")
        return

    # ── Write output files ────────────────────────────────────────────────────
    out_dir.mkdir(parents=True, exist_ok=True)

    def write_jsonl(path: Path, rows: List[dict]) -> None:
        with open(path, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"  Saved → {path}  ({len(rows)} rows)")

    write_jsonl(out_dir / "queries.jsonl",  queries_rows)
    write_jsonl(out_dir / "qrels.jsonl",    qrels_rows)
    if unresolved_rows:
        write_jsonl(out_dir / "unresolved.jsonl", unresolved_rows)
        print(f"\n  ⚠️  Open unresolved.jsonl, find the correct doc_id for each entry,")
        print(f"      then append rows to qrels.jsonl in the format:")
        print(f'      {{"query_id":"qXXX","doc_id":"A-...","relevance":2}}')

    print(f"\nDone. Next step — evaluate with:")
    print(f"  python scripts/06_rerank.py --queries {out_dir}/queries.jsonl --output {out_dir}/runs_bge_m3.jsonl")
    print(f"  python scripts/07_eval_metrics.py --queries {out_dir}/queries.jsonl --qrels {out_dir}/qrels.jsonl --runs {out_dir}/runs_bge_m3.jsonl --model BAAI/bge-reranker-v2-m3")


if __name__ == "__main__":
    main()
