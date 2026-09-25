"""Audit chunk quality across the corpus.

Bad chunks cost three times over: they pollute retrieval, they waste assessor
time during judging, and when one wins the ranking it becomes the answer. This
script counts how much of the corpus is affected and by what, so the fixes can
be ordered by how much text they actually recover.

Defect classes, each detected independently since a chunk can carry several:

  ocr_corrupt   OCR mangled the text — Vietnamese diacritics stripped and
                digits substituted into words ("c6ng", "hqc phi thpc"). The
                text is unreadable, so it can neither be retrieved sensibly
                nor quoted.
  form_blank    Form scaffolding: dotted fill-in lines, letterheads, "Kính
                gửi". Carries no regulation.
  stub          Too little content to answer anything, typically a table row
                that lost its header context.
  mislabelled   Appendix or form content wearing a Điều/Khoản label, which
                makes it look like a citable provision.
  over_budget   Longer than the bi-encoder's 258-token window, so the tail is
                silently dropped at index time and can never be matched.

Run from project root:
  python eval/audit_chunks.py
  python eval/audit_chunks.py --show ocr_corrupt --limit 5
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"

VN_DIACRITICS = set("àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợ"
                    "ùúủũụưừứửữựỳýỷỹỵđ")

WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
# A digit wedged inside a run of letters: "c6ng", "s6ch", "h0a". Legal citations
# such as "81/2021/NĐ-CP" keep their digits in separate tokens, so they do not
# trip this.
DIGIT_IN_WORD_RE = re.compile(r"[^\W\d_]+\d+[^\W\d_]+|[^\W\d_]\d(?=[^\W\d_])", re.UNICODE)
DOT_LEADER_RE = re.compile(r"[.…]{4,}")

FORM_MARKERS = (
    "kính gửi", "đơn đề nghị", "cộng hòa xã hội", "độc lập - tự do",
    "độc lập – tự do", "người làm đơn", "xác nhận của", "mẫu số",
    "phụ lục", "biểu mẫu", "họ tên cha", "hộ khẩu thường trú",
)


def diacritic_ratio(text: str) -> float:
    letters = [c for c in text.lower() if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if c in VN_DIACRITICS) / len(letters)


def digit_in_word_ratio(text: str) -> float:
    words = WORD_RE.findall(text)
    if not words:
        return 0.0
    hits = len(DIGIT_IN_WORD_RE.findall(text))
    return hits / max(len(words), 1)


def dot_ratio(text: str) -> float:
    if not text:
        return 0.0
    return sum(len(m) for m in DOT_LEADER_RE.findall(text)) / len(text)


def approx_tokens(text: str) -> int:
    """Rough token count for the bi-encoder budget.

    Vietnamese subword tokenisers emit roughly 1.6 tokens per whitespace word
    for this model; exactness is not needed to see whether a chunk is far past
    a 258-token window.
    """
    return int(len(text.split()) * 1.6)


def classify(chunk: dict) -> set[str]:
    text = (chunk.get("text") or "").strip()
    flags: set[str] = set()
    low = text.lower()

    if not text:
        flags.add("empty")
        return flags

    # OCR corruption: diacritics largely gone *and* digits substituted into
    # words. Either signal alone produces false positives — legal text quotes
    # plenty of codes, and short headings can legitimately lack diacritics.
    if len(text) > 80 and digit_in_word_ratio(text) > 0.06 and diacritic_ratio(text) < 0.12:
        flags.add("ocr_corrupt")

    if dot_ratio(text) > 0.08 or sum(1 for m in FORM_MARKERS if m in low[:400]) >= 2:
        flags.add("form_blank")

    words = len(text.split())
    if words < 12 or len(text) < 60:
        flags.add("stub")

    if (chunk.get("article") or chunk.get("khoan")) and (
        "form_blank" in flags
        or any(low.startswith(m) for m in ("phụ lục", "cộng hòa", "kính gửi",
                                           "đơn đề nghị", "biểu mẫu", "mẫu số"))
    ):
        flags.add("mislabelled")

    if approx_tokens(text) > 258:
        flags.add("over_budget")

    return flags


def main() -> None:
    ap = argparse.ArgumentParser(description="Audit chunk quality")
    ap.add_argument("--show", default=None, help="print examples of this defect")
    ap.add_argument("--limit", type=int, default=5)
    args = ap.parse_args()

    chunks: list[dict] = []
    for path in sorted(PROCESSED.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                chunks.append(json.loads(line))

    total = len(chunks)
    flagged: list[tuple[dict, set[str]]] = []
    counts: Counter = Counter()
    by_source: dict[str, Counter] = defaultdict(Counter)
    clean = 0

    for chunk in chunks:
        flags = classify(chunk)
        if flags:
            flagged.append((chunk, flags))
            for f in flags:
                counts[f] += 1
                by_source[chunk.get("source", "?")][f] += 1
        else:
            clean += 1

    any_defect = len(flagged)

    print(f"Corpus: {total} chunks from {len({c.get('source') for c in chunks})} documents\n")
    print("── Defects (a chunk may carry several) ─────────────────────────")
    for name in ("ocr_corrupt", "form_blank", "stub", "mislabelled",
                 "over_budget", "empty"):
        n = counts.get(name, 0)
        print(f"  {name:<14} {n:>5}  {100*n/total:5.1f}%")
    print(f"\n  {'ANY defect':<14} {any_defect:>5}  {100*any_defect/total:5.1f}%")
    print(f"  {'clean':<14} {clean:>5}  {100*clean/total:5.1f}%")

    # Which documents concentrate the damage — that is where a parser fix pays.
    print("\n── Worst documents by defect count ─────────────────────────────")
    ranked = sorted(by_source.items(), key=lambda kv: -sum(kv[1].values()))
    src_total = Counter(c.get("source", "?") for c in chunks)
    for source, defects in ranked[:8]:
        n = sum(defects.values())
        share = 100 * n / max(src_total[source], 1)
        top = ", ".join(f"{k}={v}" for k, v in defects.most_common(3))
        print(f"  {n:>4} defects ({share:4.0f}% of doc)  {source[:52]}")
        print(f"       {top}")

    # OCR damage is document-level: a scanned PDF is bad throughout, so the
    # remedy is re-OCR, not chunk-level filtering.
    ocr_docs = {s: d["ocr_corrupt"] for s, d in by_source.items() if d.get("ocr_corrupt")}
    if ocr_docs:
        print("\n── Documents with OCR corruption ───────────────────────────────")
        for source, n in sorted(ocr_docs.items(), key=lambda kv: -kv[1]):
            print(f"  {n:>4}/{src_total[source]:<4} chunks  {source[:56]}")

    if args.show:
        print(f"\n── Examples: {args.show} ───────────────────────────────────────")
        shown = 0
        for chunk, flags in flagged:
            if args.show in flags:
                text = " ".join((chunk.get("text") or "").split())
                loc = f"Điều {chunk.get('article')} Khoản {chunk.get('khoan')}" \
                    if chunk.get("article") else chunk.get("section_type", "?")
                print(f"\n  [{loc}] {chunk.get('source', '')[:44]} p.{chunk.get('page')}")
                print(f"  flags: {sorted(flags)}")
                print(f"  {text[:240]}")
                shown += 1
                if shown >= args.limit:
                    break


if __name__ == "__main__":
    main()
