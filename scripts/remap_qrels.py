#!/usr/bin/env python
"""
Remap qrels.jsonl doc_ids to match current processed JSONL chunks.

Strategy (in order):
  1. Exact text match
  2. Prefix substring (60 chars)
  3. Suffix substring (60 chars)
  4. Word overlap (>= 5 shared tokens, pick best match)
  5. No match → drop

Writes:
  data/eval/qrels.jsonl         – updated (TREC flat format)
  data/eval/qrels_remap_log.txt – mapping log for review
"""
import glob
import json
import re
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")


def word_tokens(s: str) -> set[str]:
    return set(re.findall(r"[a-zà-ỹA-ZÀ-Ỹ]{3,}", s.lower()))


# ── Load current chunks ───────────────────────────────────────────────────────
print("Loading current processed chunks…")
text_to_did: dict[str, str] = {}
did_to_text: dict[str, str] = {}
chunk_words: dict[str, set[str]] = {}  # did -> word set

for f in sorted(glob.glob("data/processed/*.jsonl")):
    for line in open(f, encoding="utf-8"):
        row = json.loads(line)
        t = row["text"].strip()
        did = row["doc_id"]
        text_to_did[t] = did
        did_to_text[did] = t
        chunk_words[did] = word_tokens(t)

print(f"  {len(did_to_text)} chunks loaded.")

# Pre-build list for iteration
all_texts  = list(text_to_did.items())   # [(text, did), …]
all_chunks = list(did_to_text.items())   # [(did, text), …]

# ── Load groundtruth_report ───────────────────────────────────────────────────
gt_path = Path("data/eval/groundtruth_report.jsonl")
gt_rows = [json.loads(l) for l in gt_path.open(encoding="utf-8")]
print(f"  {len(gt_rows)} groundtruth rows loaded.")


def find_doc(old_text: str, old_did: str) -> tuple[str | None, str]:
    """Return (new_doc_id, method) or (None, 'miss')."""
    t = old_text.strip()

    # 1. Exact
    if t in text_to_did:
        nd = text_to_did[t]
        return nd, ("exact_same" if nd == old_did else "exact_new")

    # 2. Prefix-60 substring
    prefix = t[:60]
    if prefix:
        for ct, cdid in all_texts:
            if prefix in ct:
                return cdid, "prefix_substr"

    # 3. Suffix-60 substring
    suffix = t[-60:]
    if suffix:
        for ct, cdid in all_texts:
            if suffix in ct:
                return cdid, "suffix_substr"

    # 4. Word overlap (best ≥5 shared tokens)
    old_words = word_tokens(t)
    if old_words:
        best_score, best_did = 0, None
        for cdid, cwords in chunk_words.items():
            overlap = len(old_words & cwords)
            if overlap > best_score:
                best_score = overlap
                best_did = cdid
        if best_score >= 5 and best_did:
            return best_did, f"word_overlap({best_score})"

    return None, "miss"


# ── Remap ─────────────────────────────────────────────────────────────────────
log_lines: list[str] = []
new_qrels: list[dict] = []

stats: dict[str, int] = {
    "exact_same": 0, "exact_new": 0,
    "prefix_substr": 0, "suffix_substr": 0,
    "word_overlap": 0, "miss": 0,
}

for row in gt_rows:
    qid = row["query_id"]
    for chunk in row.get("relevant_chunks", []):
        old_did  = chunk["doc_id"]
        old_text = chunk.get("text", "")

        new_did, method = find_doc(old_text, old_did)

        bucket = "word_overlap" if method.startswith("word_overlap") else method
        stats[bucket] += 1

        log_lines.append(f"{qid}\t{method}\t{old_did} -> {new_did or 'DROP'}")

        if new_did:
            new_qrels.append({
                "query_id": qid,
                "doc_id":   new_did,
                "relevance": 1,
            })

# ── Deduplicate: keep highest grade per (qid, doc_id) ────────────────────────
merged: dict[tuple, int] = {}
for rec in new_qrels:
    key = (rec["query_id"], rec["doc_id"])
    merged[key] = max(merged.get(key, 0), rec["relevance"])

final_qrels = [
    {"query_id": qid, "doc_id": did, "relevance": grade}
    for (qid, did), grade in sorted(merged.items())
]

# ── Write output ──────────────────────────────────────────────────────────────
out_path = Path("data/eval/qrels.jsonl")
with out_path.open("w", encoding="utf-8") as fh:
    for rec in final_qrels:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

log_path = Path("data/eval/qrels_remap_log.txt")
log_path.write_text("\n".join(log_lines), encoding="utf-8")

# ── Summary ───────────────────────────────────────────────────────────────────
total = sum(stats.values())
print(f"\nRemap summary ({total} relevance judgements):")
for method, count in stats.items():
    pct = count / max(1, total) * 100
    print(f"  {method:<18}: {count:4d}  ({pct:.1f}%)")

qids_with_rel = len({r["query_id"] for r in final_qrels})
print(f"\nOutput : {len(final_qrels)} qrel entries across {qids_with_rel} queries")
print(f"Written → {out_path}")
print(f"Log     → {log_path}")
