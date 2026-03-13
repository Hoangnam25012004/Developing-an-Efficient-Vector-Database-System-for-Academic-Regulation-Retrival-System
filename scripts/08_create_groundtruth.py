#!/usr/bin/env python
"""
Create groundtruth files (queries.jsonl, qrels.jsonl) from Q&A.JSONL.

Q&A formats handled:
  - "source" format (ids 1-100):
      {"file": "x.pdf", "page": N, "evidence": "key phrase"}
      → match by evidence substring in chunks of that file
      → fallback: match by <<<PAGE:N>>> marker
  - "sources" format (ids 101-350):
      [{"file": "x.pdf", "cite": "turnNNfileM:Lstart-Lend"}]
      → match by keyword scoring (question + answer terms) against chunks

Output (written to --out-dir):
  queries.jsonl  — {"query_id": "Q1",  "query": "..."}
  qrels.jsonl    — {"query_id": "Q1",  "relevant": ["doc_id", ...]}
  groundtruth_report.jsonl  — diagnostic: match counts, unmatched ids
"""
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional


# ────────────────────────────────────────────────────────────────────────────
# I/O helpers
# ────────────────────────────────────────────────────────────────────────────

def read_jsonl(path: Path) -> List[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_jsonl(rows: List[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


# ────────────────────────────────────────────────────────────────────────────
# Chunk index
# ────────────────────────────────────────────────────────────────────────────

def build_chunk_index(processed_dir: Path) -> Dict[str, List[dict]]:
    """Load all processed JSONL files → {file_stem: [chunks]}."""
    index: Dict[str, List[dict]] = {}
    for p in sorted(processed_dir.glob("*.jsonl")):
        stem = p.stem  # e.g. "9.-QD688-MGHP-2024"
        chunks = read_jsonl(p)
        index[stem] = chunks
    return index


def stem_from_pdf(filename: str) -> str:
    """Strip .pdf extension to get the processed JSONL stem."""
    return Path(filename).stem


# ────────────────────────────────────────────────────────────────────────────
# Matching strategies
# ────────────────────────────────────────────────────────────────────────────

def _normalise(text: str) -> str:
    """Lowercase + collapse whitespace for matching."""
    return " ".join(text.lower().split())


# Vietnamese stopwords / very common short words to ignore in keyword scoring
_STOPWORDS = {
    "là", "và", "của", "có", "trong", "với", "theo", "được", "về",
    "cho", "các", "một", "không", "này", "đó", "từ", "khi", "tại",
    "đến", "thì", "như", "hay", "hoặc", "đều", "đã", "sẽ", "phải",
    "nếu", "mà", "bao", "gồm", "tổng", "sau", "trước", "trên", "dưới",
    "bởi", "vì", "thế", "nào", "gì", "ai", "đây", "bao nhiêu",
}


def _extract_keywords(text: str, min_len: int = 4) -> List[str]:
    """Extract significant word tokens from Vietnamese text."""
    # Remove punctuation except spaces
    cleaned = re.sub(r"[^\w\s]", " ", text.lower())
    tokens = cleaned.split()
    return [t for t in tokens if len(t) >= min_len and t not in _STOPWORDS]


def score_chunks_by_keywords(chunks: List[dict], query_text: str,
                              top_k: int = 3,
                              min_score: int = 2) -> List[str]:
    """
    Score each chunk by how many query keywords it contains.
    Returns doc_ids of the top_k chunks scoring >= min_score.
    """
    keywords = _extract_keywords(query_text)
    if not keywords:
        return []

    scored = []
    for c in chunks:
        chunk_lower = c["text"].lower()
        score = sum(1 for kw in keywords if kw in chunk_lower)
        if score >= min_score:
            scored.append((score, c["doc_id"]))

    # Sort by score descending, return top_k
    scored.sort(key=lambda x: -x[0])
    return [doc_id for _, doc_id in scored[:top_k]]


def match_by_evidence(chunks: List[dict], evidence: str) -> List[str]:
    """Return doc_ids whose text contains the evidence as a substring."""
    ev = _normalise(evidence)
    if not ev:
        return []
    return [c["doc_id"] for c in chunks if ev in _normalise(c["text"])]


def match_by_page(chunks: List[dict], page: int) -> List[str]:
    """Return doc_ids whose text contains <<<PAGE:page>>>."""
    marker = f"<<<PAGE:{page}>>>"
    return [c["doc_id"] for c in chunks if marker in c["text"]]


def match_by_answer_phrases(chunks: List[dict], answer: str,
                             min_phrase_len: int = 10) -> List[str]:
    """
    Split answer into clauses; for each clause long enough, find chunks
    that contain it as a substring.  Returns union of matches.
    """
    clauses = re.split(r"[;.\n!?]", answer)
    matched = set()
    for clause in clauses:
        phrase = _normalise(clause.strip())
        if len(phrase) < min_phrase_len:
            continue
        for c in chunks:
            if phrase in _normalise(c["text"]):
                matched.add(c["doc_id"])
    return list(matched)


def match_by_answer_ngrams(chunks: List[dict], answer: str,
                           window_sizes: tuple = (2, 3, 4),
                           min_phrase_len: int = 8) -> List[str]:
    """
    Slide a word-window over the answer and search for each n-gram as a
    substring.  Catches cases where the answer paraphrases a phrase that
    appears verbatim in the chunk (e.g. '01 lớp phó').
    """
    words = answer.split()
    matched = set()
    for n in window_sizes:
        for i in range(len(words) - n + 1):
            phrase = " ".join(words[i : i + n]).lower()
            if len(phrase) < min_phrase_len:
                continue
            for c in chunks:
                if phrase in c["text"].lower():
                    matched.add(c["doc_id"])
    return list(matched)


def match_entry(qa: dict, chunk_index: Dict[str, List[dict]]) -> List[tuple]:
    """
    Resolve Q&A entry → list of (doc_id, file_stem) tuples.
    Carrying file_stem alongside doc_id prevents text-lookup collisions when
    different PDFs produce chunks with identical doc_ids.

    Uses different strategies based on Q&A format:
    - "source" format: evidence text → page marker → answer phrases → keyword scoring
    - "sources" format: answer phrases → keyword scoring on (question + answer)
    """
    if "source" in qa:
        sources = [qa["source"]]
        is_evidence_format = True
    else:
        sources = qa.get("sources", [])
        is_evidence_format = False

    all_matches: List[tuple] = []   # [(doc_id, file_stem), ...]

    for src in sources:
        file_stem = stem_from_pdf(src["file"])
        chunks = chunk_index.get(file_stem, [])
        if not chunks:
            print(f"  [WARN] No processed chunks for file: {src['file']!r}",
                  file=sys.stderr)
            continue

        if is_evidence_format:
            # ── Strategy 1: exact evidence substring ───────────────────
            evidence = src.get("evidence", "")
            if evidence:
                hits = match_by_evidence(chunks, evidence)
                if hits:
                    all_matches.extend((d, file_stem) for d in hits)
                    continue

            # ── Strategy 2: page marker ────────────────────────────────
            page = src.get("page")
            if page is not None:
                hits = match_by_page(chunks, page)
                if hits:
                    all_matches.extend((d, file_stem) for d in hits)
                    continue

            # ── Strategy 3: answer phrases fallback ────────────────────
            hits = match_by_answer_phrases(chunks, qa.get("answer", ""))
            if hits:
                all_matches.extend((d, file_stem) for d in hits)
                continue

            # ── Strategy 4: keyword scoring on evidence ─────────────────
            combined = qa.get("question", "") + " " + qa.get("answer", "")
            hits = score_chunks_by_keywords(chunks, combined, top_k=2, min_score=3)
            all_matches.extend((d, file_stem) for d in hits)

        else:
            # ── sources/cite format: try answer phrases first ──────────
            hits = match_by_answer_phrases(chunks, qa.get("answer", ""),
                                           min_phrase_len=10)
            if hits:
                all_matches.extend((d, file_stem) for d in hits)
                continue

            # ── Keyword scoring on question + answer ───────────────────
            combined = qa.get("question", "") + " " + qa.get("answer", "")
            # Try strict threshold first; fall back to top-1 if nothing found
            hits = score_chunks_by_keywords(chunks, combined, top_k=2, min_score=3)
            if not hits:
                hits = score_chunks_by_keywords(chunks, combined, top_k=1, min_score=1)
            all_matches.extend((d, file_stem) for d in hits)

    # De-duplicate while preserving order (key = doc_id + file_stem)
    seen = set()
    deduped = []
    for pair in all_matches:
        if pair not in seen:
            seen.add(pair)
            deduped.append(pair)
    return deduped


# ────────────────────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate queries.jsonl + qrels.jsonl from Q&A.JSONL"
    )
    parser.add_argument(
        "--qa",
        default="C:/AcademicRegulation/data/Q&A/Q&A.JSONL",
        help="Path to the Q&A JSONL file",
    )
    parser.add_argument(
        "--processed-dir",
        default="C:/AcademicRegulation/data/processed",
        help="Directory containing per-document processed JSONL files",
    )
    parser.add_argument(
        "--out-dir",
        default="data/eval",
        help="Output directory for queries.jsonl and qrels.jsonl",
    )
    args = parser.parse_args()

    qa_path = Path(args.qa)
    processed_dir = Path(args.processed_dir)
    out_dir = Path(args.out_dir)

    print(f"[1/4] Loading Q&A from {qa_path}")
    qa_entries = read_jsonl(qa_path)
    print(f"      {len(qa_entries)} entries")

    print(f"[2/4] Building chunk index from {processed_dir}")
    chunk_index = build_chunk_index(processed_dir)
    total_chunks = sum(len(v) for v in chunk_index.values())
    print(f"      {len(chunk_index)} files, {total_chunks} chunks")

    # Build a file-scoped text lookup: {file_stem: {doc_id: text}}
    # Using a flat dict caused silent overwrites when different PDFs produced
    # chunks with identical doc_ids (1,235 collisions found across 34 files).
    file_doc_text: Dict[str, Dict[str, str]] = {}
    for file_stem, chunks in chunk_index.items():
        file_doc_text[file_stem] = {c["doc_id"]: c["text"] for c in chunks}

    print("[3/4] Matching Q&A to chunks …")
    queries_rows: List[dict] = []
    qrels_rows: List[dict] = []
    report_rows: List[dict] = []
    unmatched_ids: List[int] = []

    for qa in qa_entries:
        qid = f"Q{qa['id']}"
        # relevant is now [(doc_id, file_stem), ...]
        relevant = match_entry(qa, chunk_index)

        queries_rows.append({"query_id": qid, "query": qa["question"]})
        relevant_with_text = [
            {
                "doc_id": doc_id,
                "text": file_doc_text.get(file_stem, {}).get(doc_id, ""),
            }
            for doc_id, file_stem in relevant
        ]
        qrels_rows.append({"query_id": qid, "relevant": relevant_with_text})

        status = "ok" if relevant else "UNMATCHED"
        if not relevant:
            unmatched_ids.append(qa["id"])
            print(f"  [UNMATCHED] id={qa['id']} q={qa['question'][:60]!r}",
                  file=sys.stderr)

        # Build source label for report
        if "source" in qa:
            src_label = qa["source"]["file"]
            evidence = qa["source"].get("evidence", "")
        else:
            src_label = "; ".join(s["file"] for s in qa.get("sources", []))
            evidence = ""

        # Enrich each relevant doc with its chunk text from the correct file
        relevant_chunks = []
        for doc_id, file_stem in relevant:
            text = file_doc_text.get(file_stem, {}).get(doc_id, "")
            relevant_chunks.append({
                "doc_id": doc_id,
                "text": text,
            })

        report_rows.append({
            "query_id": qid,
            "status": status,
            "question": qa["question"],
            "answer": qa.get("answer", ""),
            "evidence": evidence,
            "source_file": src_label,
            "n_relevant": len(relevant),
            "relevant_chunks": relevant_chunks,
        })

    print(f"[4/4] Writing output to {out_dir}/")
    write_jsonl(queries_rows, out_dir / "queries.jsonl")
    write_jsonl(qrels_rows,   out_dir / "qrels.jsonl")
    write_jsonl(report_rows,  out_dir / "groundtruth_report.jsonl")

    matched = len(qa_entries) - len(unmatched_ids)
    print(f"\n=== Done ===")
    print(f"  Total Q&A    : {len(qa_entries)}")
    print(f"  Matched      : {matched} ({100*matched/len(qa_entries):.1f}%)")
    print(f"  Unmatched    : {len(unmatched_ids)}")
    if unmatched_ids:
        print(f"  Unmatched IDs: {unmatched_ids}")
    print(f"  Output files : {out_dir}/queries.jsonl")
    print(f"               : {out_dir}/qrels.jsonl")
    print(f"  Report       : {out_dir}/groundtruth_report.jsonl")


if __name__ == "__main__":
    main()
