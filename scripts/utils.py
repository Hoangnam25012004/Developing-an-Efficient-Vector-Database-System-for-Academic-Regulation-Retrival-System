import json
import math
import os
import pickle
import re
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import regex as re2
import ujson
import yaml
from unidecode import unidecode

# Tokenization
try:
    import tiktoken

    _enc = tiktoken.get_encoding("cl100k_base")
except Exception:
    _enc = None


def load_config(path: str = "configs/default.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def ensure_dir(p: str | Path) -> Path:
    p = Path(p)
    p.mkdir(parents=True, exist_ok=True)
    return p


def save_jsonl(rows: Iterable[dict], out_path: str | Path) -> None:
    with open(out_path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(ujson.dumps(row, ensure_ascii=False) + "\n")


def read_jsonl(path: str | Path) -> List[dict]:
    items = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                items.append(ujson.loads(line))
    return items


def tokenize_tokens(text: str) -> List[int]:
    if _enc:
        return _enc.encode(text)
    # naive fallback
    return [1 for _ in re.findall(r"\w+", text, flags=re.UNICODE)]


def token_count(text: str) -> int:
    return len(tokenize_tokens(text))


def chunk_by_tokens(
    text: str, target_tokens: int = 220, overlap_ratio: float = 0.25
) -> List[str]:
    """Sliding-window token chunking with overlap."""
    if target_tokens <= 0:
        return [text]
    tokens = tokenize_tokens(text)
    if len(tokens) <= target_tokens:
        return [text]
    step = max(1, int(target_tokens * (1 - overlap_ratio)))
    chunks = []
    for start in range(0, len(tokens), step):
        end = start + target_tokens
        if start >= len(tokens):
            break
        piece_tokens = tokens[start:end]
        # reconstruct approx text slice by character proportion
        # (tiktoken lacks decode here; so approximate using word boundaries)
        # simple approach: map token spans to character spans using proportion
        proportion = len(piece_tokens) / len(tokens)
        approx_chars = max(1, int(len(text) * proportion))
        # naive slicing; then expand to nearest sentence boundary if possible
        rough = (
            text[:approx_chars]
            if not chunks
            else text[
                int(len(text) * (start / len(tokens))) : int(
                    len(text) * (end / len(tokens))
                )
            ]
        )
        # trim at sentence boundary
        m = re.search(r"(.+?[\.!?…](\s+|$))", rough, flags=re.S)
        if m:
            rough = m.group(1)
        chunks.append(rough.strip())
        if end >= len(tokens):
            break
    # dedup trivial overlaps
    dedup = []
    seen = set()
    for c in chunks:
        key = c[:80]
        if key not in seen and c:
            seen.add(key)
            dedup.append(c)
    return dedup if dedup else [text]


def vnfold(s: str) -> str:
    return unidecode(s or "").lower()


# --- Vietnamese structure regex (accent-insensitive via vnfold) ---
CHAPTER_RE = re2.compile(r"^\s*chuong\s+([ivxlcdm]+|\d+)\b", re2.I)
ARTICLE_RE = re2.compile(r"^\s*dieu\s+(\d+[a-z]?)\b", re2.I)
CLAUSE_RE = re2.compile(r"^\s*khoan\s+(\d+)\b", re2.I)
# Typical formats: "điểm a)", "điểm a.", or leading dash + letter "- a)"
POINT_RE = re2.compile(r"^(?:\s*diem\s+([a-z]))|(?:^\s*[-•]\s*([a-z]))", re2.I)


def detect_structure(lines: List[str]) -> List[Dict[str, Any]]:
    """Parse headings to build a list of segments with hierarchical path.
    Returns items: {start_idx, end_idx (filled later), path_hierarchy, article_no, clause_no, point}
    """
    segs = []
    current = {"chapter": None, "article": None, "clause": None, "point": None}

    for idx, line in enumerate(lines):
        f = vnfold(line)
        matched = False
        if CHAPTER_RE.match(f):
            ch = CHAPTER_RE.match(f).group(1)
            current["chapter"] = f"Chương {ch.upper()}"
            current["article"] = None
            current["clause"] = None
            current["point"] = None
            matched = True
        elif ARTICLE_RE.match(f):
            a = ARTICLE_RE.match(f).group(1)
            current["article"] = f"Điều {a}"
            current["clause"] = None
            current["point"] = None
            matched = True
        elif CLAUSE_RE.match(f):
            k = CLAUSE_RE.match(f).group(1)
            current["clause"] = f"Khoản {k}"
            current["point"] = None
            matched = True
        else:
            pm = POINT_RE.match(f)
            if pm:
                p = pm.group(1) or pm.group(2)
                current["point"] = f"Điểm {p}"
                matched = True
        if matched:
            path = [
                x
                for x in [
                    current["chapter"],
                    current["article"],
                    current["clause"],
                    current["point"],
                ]
                if x
            ]
            segs.append(
                {
                    "start_idx": idx,
                    "end_idx": None,
                    "path_hierarchy": path,
                    "article_no": _extract_int(current.get("article")),
                    "clause_no": _extract_int(current.get("clause")),
                    "point": _extract_point(current.get("point")),
                }
            )
    # fill end_idx
    for i in range(len(segs)):
        segs[i]["end_idx"] = (
            segs[i + 1]["start_idx"] if i + 1 < len(segs) else len(lines)
        )
    return segs


def _extract_int(s: Optional[str]) -> Optional[int]:
    if not s:
        return None
    m = re.search(r"(\d+)", s)
    return int(m.group(1)) if m else None


def _extract_point(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    m = re.search(r"([a-z])", vnfold(s))
    return m.group(1) if m else None


def build_doc_id(
    article_no: Optional[int], clause_no: Optional[int], point: Optional[str], i: int
) -> str:
    a = article_no if article_no is not None else "NA"
    c = clause_no if clause_no is not None else "NA"
    p = point if point is not None else "NA"
    return f"A-{a}-{c}-{p}-{i}"


def remove_headers_footers(pages: List[str]) -> List[str]:
    """Heuristic removal of headers/footers/watermarks: lines that repeat in >50% pages
    in the first/last 3 lines of pages; also drop page-number-only lines and common patterns.
    """
    from collections import Counter

    first_lines, last_lines = [], []
    for p in pages:
        ls = [l.strip() for l in p.splitlines() if l.strip()]
        if not ls:
            continue
        first_lines.extend(ls[:3])
        last_lines.extend(ls[-3:])
    counts = Counter(first_lines + last_lines)
    threshold = max(2, int(0.5 * max(1, len(pages))))
    repetitive = {s for s, c in counts.items() if c >= threshold}

    cleaned_pages = []
    for p in pages:
        new_lines = []
        for l in p.splitlines():
            lt = l.strip()
            lf = vnfold(lt)
            if not lt:
                continue
            # page numbers
            if re2.search(r"^(page\s*)?\d+\s*/?\s*\d*$", lf):
                continue
            if lt in repetitive:
                continue
            # common watermarks
            if re2.search(r"(watermark|confidential|draft)", lf):
                continue
            new_lines.append(lt)
        cleaned_pages.append("\n".join(new_lines))
    return cleaned_pages


def text_from_pdf(path: str) -> Tuple[str, List[str]]:
    """Return full_text and per-page texts."""
    import pdfplumber

    pages_raw = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            pages_raw.append(page.extract_text(x_tolerance=1, y_tolerance=1) or "")
    return "\n\n".join(pages_raw), pages_raw


def text_from_docx(path: str) -> str:
    from docx import Document

    doc = Document(path)
    return "\n".join(p.text for p in doc.paragraphs)


def list_files_recursively(in_paths: List[str]) -> List[Path]:
    out = []
    for p in in_paths:
        pth = Path(p)
        if pth.is_dir():
            for ext in ("*.pdf", "*.docx", "*.DOCX", "*.PDF"):
                out.extend(sorted(pth.rglob(ext)))
        elif pth.exists():
            out.append(pth)
    return out
