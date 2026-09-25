"""
Step 01 — Parse PDF and DOCX documents and chunk them into JSONL.

PDFs go through PyMuPDF (+ OCR fallback); DOCX files through
src/ingest/docx_extract.py. Both then share the chunking and writing below.

Chunking uses a single content-driven algorithm (HPAD — Heading-Pattern
Auto-Detect): it scans a universal catalog of heading regexes, picks the
dominant pattern per level (1=major / 2=mid / 3=leaf), and splits the document
hierarchically. Documents with no detectable heading pattern fall back to the
paragraph chunker. There is no doc_type-based routing — the chunker decides how
to split purely from content.

Document metadata (doc_group / doc_type / issuing_body) comes from the
data/<group>/ folder the file lives in, resolved through the data/groups.json
registry — set by the user at upload time, not guessed from content. Only the
reference number is read from the body, by extract_doc_number(). This metadata
is orthogonal to chunking; it feeds citation footers and the /sources API.

Each chunk JSONL line carries:
  chunk_id, source, source_path, page, chunk_index, text,
  doc_group, doc_type, doc_number, issuing_body,
  section_type, chapter, chapter_title, article, article_title, khoan
Documents uploaded through the ingestion flow (they have a <name>.meta.json
sidecar) additionally carry: file_type, language, level_labels, ingest_version.
Documents without a sidecar produce exactly the fields above.

OCR fallback: pages with too little text or too many '?' (font-encoding
failure) are rendered at high DPI and passed to Tesseract (lang=vie+eng).
"""
from __future__ import annotations
from collections import defaultdict
from contextlib import contextmanager

import argparse
import hashlib
import json
import os
import re
import sys
import time
import warnings
from pathlib import Path

import fitz  # PyMuPDF
import yaml

# ── Optional OCR deps ─────────────────────────────────────────────────────────
try:
    import pytesseract
    from PIL import Image, ImageFilter, ImageOps
    _OCR_AVAILABLE = True
except ImportError:
    _OCR_AVAILABLE = False
    warnings.warn(
        "pytesseract / Pillow not installed — scanned PDFs will yield 0 chunks.\n"
        "Fix: pip install pytesseract Pillow  (+ install Tesseract binary)",
        stacklevel=1,
    )

# OCR tunables
OCR_TEXT_THRESHOLD = 50            # below this many chars → try OCR
OCR_DPI = 300                      # rendering DPI (was 200 — higher = sharper)
OCR_CORRUPT_THRESHOLD = 0.10       # > this fraction of '?' → corrupt
OCR_PSM = 6                        # Page Segmentation Mode 6 = uniform block of text

# Several scans in this corpus ship a text layer produced by a non-Vietnamese
# OCR engine: plenty of characters, not one diacritic ("DQc l$p - Tg do" for
# "Độc lập - Tự do"). Such a page is long enough to clear OCR_TEXT_THRESHOLD
# and contains no '?', so neither existing check fires and the garbage is
# indexed as if it were text. Measured over this corpus the two populations do
# not overlap at all — 37 broken pages sit at exactly 0.000 while the lowest
# healthy page is 0.239 — so any threshold in between is safe.
OCR_MIN_DIACRITIC_RATIO = 0.05
OCR_DIACRITIC_MIN_CHARS = 200      # below this, absent diacritics prove nothing

_VN_DIACRITICS = frozenset(
    "àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợ"
    "ùúủũụưừứửữựỳýỷỹỵđ"
)

# ── Language profile ──────────────────────────────────────────────────────────
# Structural detection is already language-agnostic: HEADING_CATALOG carries
# Vietnamese, English and bare-numeric patterns, and detect_pattern() keeps
# whichever the document actually uses. Only the heuristics below assume
# Vietnamese, so they are the whole of what a new corpus needs to change —
# populated from config by configure(), with these values as defaults.
_PROFILE: dict = {
    "ocr_lang": "vie+eng",
    "script_check": {"enabled": True, "min_ratio": OCR_MIN_DIACRITIC_RATIO,
                     "min_chars": OCR_DIACRITIC_MIN_CHARS},
    "form_markers": (
        "kính gửi", "đơn đề nghị", "cộng hòa xã hội", "độc lập - tự do",
        "độc lập – tự do", "người làm đơn", "họ tên cha", "họ tên mẹ",
        "hộ khẩu thường trú", "xác nhận của", "mẫu số", "biểu mẫu",
    ),
}


def configure(cfg: dict) -> None:
    """Load the language profile from config. Absent keys keep their default."""
    chunk_cfg = (cfg or {}).get("chunking", {}) or {}
    if "ocr_lang" in chunk_cfg:
        _PROFILE["ocr_lang"] = chunk_cfg["ocr_lang"]
    if "form_markers" in chunk_cfg:
        _PROFILE["form_markers"] = tuple(
            str(m).lower() for m in chunk_cfg["form_markers"]
        )
    if "script_check" in chunk_cfg:
        _PROFILE["script_check"] = {**_PROFILE["script_check"],
                                    **(chunk_cfg["script_check"] or {})}


# A corpus can mix languages, so the profile above (global, from `chunking:`)
# is only the default. A document declared in another language borrows an
# override for the time it is being parsed. Vietnamese — the language of the
# whole original corpus — gets no override at all, which is what keeps the
# output for those documents byte-identical.
_DEFAULT_LANGUAGE_OVERRIDES = {
    "en": {"ocr_lang": "eng", "script_check": False, "vi_normalization": False},
}


def profile_overrides(language: str | None, cfg: dict | None = None) -> dict:
    """Profile overrides for a document's language ({} = use the default)."""
    if not language:
        return {}
    langs = (((cfg or {}).get("ingest") or {}).get("languages") or {})
    if language in langs:
        return dict(langs[language] or {})
    return dict(_DEFAULT_LANGUAGE_OVERRIDES.get(language, {}))


@contextmanager
def language_profile(overrides: dict | None):
    """Apply profile overrides while one document is parsed, then restore."""
    if not overrides:
        yield
        return
    saved = {k: (dict(v) if isinstance(v, dict) else v) for k, v in _PROFILE.items()}
    try:
        if "ocr_lang" in overrides:
            _PROFILE["ocr_lang"] = str(overrides["ocr_lang"])
        if "script_check" in overrides:
            sc = overrides["script_check"]
            if isinstance(sc, dict):
                _PROFILE["script_check"] = {**_PROFILE["script_check"], **sc}
            else:
                _PROFILE["script_check"] = {**_PROFILE["script_check"], "enabled": bool(sc)}
        if "vi_normalization" in overrides:
            _PROFILE["vi_normalization"] = bool(overrides["vi_normalization"])
        if "form_markers" in overrides:
            _PROFILE["form_markers"] = tuple(str(m).lower() for m in overrides["form_markers"] or ())
        yield
    finally:
        _PROFILE.clear()
        _PROFILE.update(saved)


def load_config(path: str = "config.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ══════════════════════════════════════════════════════════════════════════════
#  OCR — improved with image preprocessing for legal documents
# ══════════════════════════════════════════════════════════════════════════════

def _preprocess_for_ocr(img: "Image.Image") -> "Image.Image":
    """
    Pre-process image for better Tesseract accuracy on legal docs:
      1. Convert to grayscale
      2. Apply slight sharpening filter
      3. Auto-contrast normalisation
    Returns PIL Image ready for Tesseract.
    """
    # Grayscale removes color noise from scans
    img = img.convert("L")
    # Auto-contrast spreads pixel values across full 0-255 range
    img = ImageOps.autocontrast(img, cutoff=1)
    # Sharpen edges to make Vietnamese diacritics clearer
    img = img.filter(ImageFilter.SHARPEN)
    return img


def _ocr_page(page: fitz.Page) -> str:
    """Render a PDF page to image, preprocess, and run Tesseract OCR (vie + eng)."""
    if not _OCR_AVAILABLE:
        return ""
    try:
        mat = fitz.Matrix(OCR_DPI / 72, OCR_DPI / 72)
        pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        img = _preprocess_for_ocr(img)
        # PSM 6 = uniform block of text (best for legal pages)
        text = pytesseract.image_to_string(
            img, lang=_PROFILE["ocr_lang"], config=f"--psm {OCR_PSM} --oem 1"
        ).strip()
        # The cleanup rewrites Vietnamese heading misreads ("Dieu 5" → "Điều 5");
        # a document in another language turns it off via its profile.
        return _post_ocr_cleanup(text) if _PROFILE.get("vi_normalization", True) else text
    except Exception as exc:
        warnings.warn(f"OCR failed on page {page.number + 1}: {exc}")
        return ""


def _post_ocr_cleanup(text: str) -> str:
    """Fix common OCR errors in Vietnamese legal text."""
    if not text:
        return text
    # Common OCR confusions in Vietnamese legal docs
    fixes = [
        (r'\bDi[eê]u\s+(\d+)', r'Điều \1'),       # "Dieu 5" → "Điều 5"
        (r'\bKho[ạa]n\s+(\d+)', r'Khoản \1'),     # "Khoan 1" → "Khoản 1"
        (r'\bCh[uư][ơo]ng\s+([IVXLCDM]+)', r'Chương \1'),
        (r'\bM[uụ]c\s+(\d+)', r'Mục \1'),
        # Collapse multi-space
        (r'[ \t]{2,}', ' '),
        # Normalise newlines
        (r'\n{3,}', '\n\n'),
    ]
    for pattern, repl in fixes:
        text = re.sub(pattern, repl, text)
    return text.strip()


def _diacritic_ratio(text: str) -> float:
    """Share of alphabetic characters carrying a Vietnamese diacritic."""
    letters = [c for c in text.lower() if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if c in _VN_DIACRITICS) / len(letters)


def _is_corrupt(text: str) -> bool:
    """True when the extracted text cannot be trusted as Vietnamese.

    Two independent failures are caught: font-encoding loss, which shows up as
    '?' replacement characters, and a broken text layer on a scan, which shows
    up as running Vietnamese prose with no diacritics at all.
    """
    if not text:
        return True
    alnum = sum(c.isalnum() for c in text)
    if alnum == 0:
        return True
    if text.count("?") / alnum > OCR_CORRUPT_THRESHOLD:
        return True

    check = _PROFILE["script_check"]
    if (
        check.get("enabled", True)
        and len(text) >= check.get("min_chars", OCR_DIACRITIC_MIN_CHARS)
        and _diacritic_ratio(text) < check.get("min_ratio", OCR_MIN_DIACRITIC_RATIO)
    ):
        return True
    return False


def extract_pages(pdf_path: Path, exclude_bboxes: dict[int, list] | None = None,
                  ocr_log: list[int] | None = None) -> list[dict]:
    """Return list of {page, text} dicts. Falls back to OCR for scanned/corrupt pages.

    `ocr_log`, when given, receives the numbers of the pages that were OCR'd,
    so ingestion can report them; it does not change what is extracted.

    If `exclude_bboxes` is supplied, text blocks whose bounding box lies
    mostly (>50% area) inside any rect for that page are dropped — used to
    redact table regions so the paragraph chunker doesn't dump the same
    table content column-by-column as flat noise. Tables are handled
    separately by extract_tables(); see process_pdf() for orchestration.

    OCR fallback: decided on RAW text length (pre-redaction). When OCR
    runs, the whole page is OCR'd — bbox redaction is skipped because OCR
    text carries no layout info.
    """
    exclude_bboxes = exclude_bboxes or {}
    doc = fitz.open(str(pdf_path))
    pages = []
    ocr_pages: list[int] = []

    for i, page in enumerate(doc, start=1):
        raw_text = page.get_text("text")
        raw_clean = re.sub(r"\n{3,}", "\n\n", raw_text).strip()

        # OCR fallback decision uses RAW text (before any redaction)
        if len(raw_clean) < OCR_TEXT_THRESHOLD or _is_corrupt(raw_clean):
            ocr_text = _ocr_page(page)
            if ocr_text and not _is_corrupt(ocr_text):
                text = re.sub(r"\n{3,}", "\n\n", ocr_text).strip()
                if text:
                    pages.append({"page": i, "text": text})
                    ocr_pages.append(i)
                continue

        # Digital text path — optionally redact table regions
        tabs_bboxes = exclude_bboxes.get(i, [])
        if tabs_bboxes:
            kept: list[str] = []
            for b in page.get_text("blocks"):
                if len(b) < 5:
                    continue
                block_type = b[6] if len(b) > 6 else 0
                if block_type != 0:  # skip image blocks
                    continue
                x0, y0, x1, y1, btext = b[0], b[1], b[2], b[3], b[4]
                if not btext or not btext.strip():
                    continue
                block_area = max((x1 - x0) * (y1 - y0), 1.0)
                inside = False
                for tb in tabs_bboxes:
                    inter = fitz.Rect(x0, y0, x1, y1) & tb
                    if not inter.is_valid or inter.is_empty:
                        continue
                    inter_area = (inter.x1 - inter.x0) * (inter.y1 - inter.y0)
                    if inter_area / block_area > 0.5:
                        inside = True
                        break
                if not inside:
                    kept.append(btext)
            text = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
        else:
            text = raw_clean

        if text:
            pages.append({"page": i, "text": text})

    doc.close()
    if ocr_pages:
        print(f"    [OCR] {pdf_path.name}: applied OCR on pages {ocr_pages}")
    if ocr_log is not None:
        ocr_log.extend(ocr_pages)
    return pages


# ══════════════════════════════════════════════════════════════════════════════
#  Table extraction — additive layer on top of text chunking
# ══════════════════════════════════════════════════════════════════════════════
#
# PyMuPDF >= 1.23 has page.find_tables() which detects ruled tables and returns
# 2D row arrays. We use it to emit one self-contained chunk per data row,
# preserving the column→value mapping that flat text extraction destroys.
# These chunks live alongside the regular paragraph/structured chunks.
# Failures (no tables, library too old) are swallowed — chunking still works.

# A row is a "data row" when its first cell looks like an item identifier.
# Supports flat numbering ("1.", "2", "12.") AND hierarchical numbering
# ("1.1", "1.1.3", "1.2.5") common in Vietnamese rubric / criteria tables.
# Without the hierarchical case, every sub-criterion gets merged into the
# parent row's pending chunk → table collapses into one giant row.
_RE_TABLE_DATA_ROW_FIRST_CELL = re.compile(r'^\s*\d+(?:\.\d+)*\.?\s*$')


def _clean_cell(s: str) -> str:
    if not s:
        return ""
    return re.sub(r'\s+', ' ', s).strip()


# A header row's cells are all label-like (short phrases, not sentences).
# Continuation rows from a multi-page table carry mid-sentence text (≥ 150
# chars typical) — this length check distinguishes them and prevents the
# continuation from being absorbed into the column labels.
#
# Threshold 100 sits comfortably between:
#   - longest legitimate merged header: "Số lần vi phạm và hình thức xử lý
#     (Số lần tính trong cả khoá học)" = 65 chars
#   - shortest realistic continuation cell content: 150+ chars
_HEADER_MAX_CELL_LEN = 100


def _is_header_candidate(row: list[str]) -> bool:
    """True if the row looks like a header (no cell holds a long sentence)."""
    non_empty = [_clean_cell(c) for c in row if _clean_cell(c)]
    if not non_empty:
        return False
    return max(len(c) for c in non_empty) <= _HEADER_MAX_CELL_LEN


def _build_column_labels(header_rows: list[list[str]], n_cols: int) -> list[str]:
    """Build a label per column from stacked header rows.
    - Top-level row: forward-fill horizontally (handles merged spans like
      "Số lần vi phạm và hình thức xử lý" covering several sub-columns).
    - Sub-header rows: NO forward-fill — empty cell means the column's only
      label is its parent (e.g. "Ghi chú" has no sub-header)."""
    filled: list[list[str]] = []
    for ri, hr in enumerate(header_rows):
        hr = list(hr) + [""] * (n_cols - len(hr))
        cleaned = [_clean_cell(c).rstrip(' (-:.,—–') for c in hr]
        if ri == 0:
            last = ""
            ff = []
            for v in cleaned:
                if v:
                    last = v
                ff.append(v if v else last)
            filled.append(ff)
        else:
            filled.append(cleaned)

    labels: list[str] = []
    for c in range(n_cols):
        seen, parts = set(), []
        for row in filled:
            v = row[c]
            if v and v not in seen:
                seen.add(v)
                parts.append(v)
        labels.append(" — ".join(parts) if parts else f"Cột {c+1}")
    return labels


def _finalize_pending(p: dict) -> dict:
    """Convert a pending row state into a final chunk dict.

    The row carries its table's caption. On its own a row reads "STT: 1 |
    Nội dung: Hỗ trợ học tập" — true of a scholarship table, a disability
    support table and a fee schedule alike, so neither a retriever nor a human
    assessor can tell which. Repeating the caption on every row costs a little
    duplication and makes each row independently interpretable, which is what
    an index of independent units needs.
    """
    caption = (p.get("_caption") or "").strip()
    head = f"[Bảng {p['_table_idx']+1} · trang {p['_first_page']} · dòng {p['_row_id']}]"
    lines = [f"{caption}\n{head}" if caption else head]
    for ci, val in enumerate(p["_cells"]):
        if val:
            lines.append(f"{p['_labels'][ci]}: {val}")
    return {
        "page": p["_first_page"],
        "section_type": "table_row",
        "text": "\n".join(lines),
        "table_caption": caption,
    }


# Lines that sit above a table but describe the document, not the table.
_CAPTION_SKIP = re.compile(
    r"^\s*(cộng hòa|độc lập|trường đại học|đại học quốc gia|số\s*:|kính gửi)",
    re.IGNORECASE,
)


def _table_caption(page: "fitz.Page", bbox, max_chars: int = 160) -> str:
    """Text immediately above a table — usually its title.

    Looks only just above the table (within `window` points) so a caption is
    not invented from unrelated body text on a sparse page.
    """
    window = 90
    try:
        rect = fitz.Rect(bbox)
        blocks = page.get_text("blocks")
    except Exception:
        return ""

    candidates = []
    for block in blocks:
        y1, text = block[3], block[4]
        if y1 <= rect.y0 and rect.y0 - y1 <= window:
            line = " ".join(str(text).split())
            if _usable_caption(line):
                candidates.append((y1, line))

    if not candidates:
        return ""
    # Nearest usable line above the table.
    candidates.sort()
    return candidates[-1][1][:max_chars].strip()


# Openings that mark a heading rather than running prose.
_CAPTION_TITLE_START = re.compile(
    r"^\s*(bảng|biểu|phụ\s*lục|mẫu|khung|tiêu\s*chí|danh\s*mục|mức|"
    r"điều\s+\d+|chương\s+[IVXLCDM\d]+)",
    re.IGNORECASE,
)


def _usable_caption(line: str) -> bool:
    """Accept only heading-like lines.

    The nearest line above a table is often just the previous sentence of the
    body — "của Hiệu trưởng Trường Đại học Quốc tế)" — and stamping that onto
    every row adds noise to the whole table. A wrong caption is worse than
    none, so partial coverage with correct captions is the better trade.
    """
    if not line or _CAPTION_SKIP.match(line):
        return False
    letters = sum(c.isalpha() for c in line)
    if letters < 8 or letters / len(line) < 0.4:
        return False  # rules, dotted leaders, mostly-numeric rows
    if len(line) > 160:
        return False  # a paragraph, not a title

    if _CAPTION_TITLE_START.match(line):
        return True
    # Vietnamese legal titles are conventionally set in capitals.
    alpha = [c for c in line if c.isalpha()]
    return bool(alpha) and sum(1 for c in alpha if c.isupper()) / len(alpha) > 0.7


def _table_rows_to_chunks(
    rows: list[list[str]],
    page_no: int,
    table_idx: int,
    carry_in: dict | None = None,
    caption: str = "",
) -> tuple[list[dict], dict | None]:
    """Convert a single extracted table into one chunk per data row.

    Multi-page handling:
      carry_in  – a pending row started on a previous page that may continue
                  here (the table got cut by a page break in the middle of a
                  row, leaving the next page's first data row with an empty
                  first cell).
      carry_out – this page's last pending row, returned so the caller can
                  pass it as carry_in for the next page.

    Header detection: leading rows whose cells are ALL short (≤ 50 chars)
    are headers. The first row with long cells terminates header detection —
    this is what stops a continuation row (empty first cell + long body) from
    being absorbed into the column labels.
    """
    if not rows or len(rows) < 2:
        return [], carry_in
    n_cols = max(len(r) for r in rows)

    header_rows: list[list[str]] = []
    data_start: int | None = None
    for ri, row in enumerate(rows):
        first_cell = _clean_cell(row[0] if row else "")
        if _RE_TABLE_DATA_ROW_FIRST_CELL.match(first_cell):
            data_start = ri
            break
        if _is_header_candidate(row):
            header_rows.append(row)
        else:
            # Long-content row before any data row → continuation from prev page.
            data_start = ri
            break

    if data_start is None:
        return [], carry_in  # entire table is header — nothing to emit

    if header_rows:
        labels = _build_column_labels(header_rows, n_cols)
    elif carry_in is not None:
        labels = carry_in["_labels"]  # reuse labels from earlier page
    else:
        return [], None  # no headers AND no carry — cannot label anything

    chunks: list[dict] = []
    pending: dict | None = carry_in

    for row in rows[data_start:]:
        row = list(row) + [""] * (n_cols - len(row))
        cells = [_clean_cell(c) for c in row]
        first_cell = cells[0] if cells else ""
        is_data_row_start = bool(_RE_TABLE_DATA_ROW_FIRST_CELL.match(first_cell))

        if is_data_row_start:
            # New row begins — flush the previous pending one.
            if pending:
                chunks.append(_finalize_pending(pending))
            # Preserve hierarchical dots so the row label reads "dòng 1.1.3"
            # instead of "dòng 113" (which loses the structure).
            row_id = first_cell.strip().rstrip('.').strip() or "?"
            pending = {
                "_cells": cells,
                "_labels": labels,
                "_table_idx": table_idx,
                "_first_page": page_no,
                "_row_id": row_id,
                # A table split across pages keeps the caption from where it
                # started; continuation pages rarely repeat the title.
                "_caption": caption or (carry_in or {}).get("_caption", ""),
            }
        else:
            # Continuation row — merge cell-by-cell into pending.
            if pending is None:
                continue  # orphan continuation: drop to avoid corruption
            for ci in range(min(n_cols, len(pending["_cells"]))):
                val = cells[ci] if ci < len(cells) else ""
                if not val:
                    continue
                if pending["_cells"][ci]:
                    pending["_cells"][ci] = f"{pending['_cells'][ci]} {val}"
                else:
                    pending["_cells"][ci] = val

    # Don't flush the last pending — it may continue on the next page.
    return chunks, pending


def extract_tables(pdf_path: Path) -> tuple[list[dict], dict[int, list]]:
    """Extract all tables in the PDF.

    Returns (chunks, bboxes_by_page):
      chunks         – one dict per table data row, cross-page-aware.
      bboxes_by_page – {page_no: [fitz.Rect, ...]} so the caller can redact
                       these regions from page text (avoids the paragraph
                       chunker dumping table content column-by-column).
    """
    out: list[dict] = []
    bboxes_by_page: dict[int, list] = {}
    try:
        doc = fitz.open(str(pdf_path))
    except Exception as exc:
        warnings.warn(f"Cannot open {pdf_path.name} for table extraction: {exc}")
        return out, bboxes_by_page

    carry: dict | None = None
    try:
        for i, page in enumerate(doc, start=1):
            try:
                tabs = page.find_tables()
            except Exception:
                continue
            for ti, table in enumerate(tabs.tables):
                try:
                    rows = table.extract()
                except Exception:
                    continue
                try:
                    bboxes_by_page.setdefault(i, []).append(fitz.Rect(table.bbox))
                except Exception:
                    pass
                caption = _table_caption(page, table.bbox)
                chunks, carry = _table_rows_to_chunks(
                    rows, i, ti, carry_in=carry, caption=caption
                )
                out.extend(chunks)
    finally:
        doc.close()

    # Flush the very last pending row (no next page to absorb it).
    if carry:
        out.append(_finalize_pending(carry))

    return out, bboxes_by_page


# ══════════════════════════════════════════════════════════════════════════════
#  Group registry  +  document-number extraction
# ══════════════════════════════════════════════════════════════════════════════
#
# A document's GROUP is determined by the data/<group>/ folder it lives in — set
# by the user at upload time — NOT guessed from content. A small registry
# (data/groups.json) maps each group folder to its display metadata (label,
# doc_type, issuing_body). The only field that still needs the document body is
# the official reference number, recovered by extract_doc_number().

GROUPS_JSON = Path("data/groups.json")


def load_group_registry(path: Path = GROUPS_JSON) -> dict:
    """Load the group → metadata registry. Returns {} if missing/invalid."""
    try:
        with Path(path).open(encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def group_meta(group: str, registry: dict) -> dict:
    """Resolve a group folder name to doc-level metadata.

    Unknown / unregistered folders still yield a usable group (the folder name)
    with empty type/body, so newly created groups work before being described
    in the registry.
    """
    entry = registry.get(group, {}) if group else {}
    return {
        "doc_group":    group or "ungrouped",
        "doc_type":     entry.get("doc_type", ""),
        "issuing_body": entry.get("issuing_body", ""),
    }


# Reference-number patterns, tried in order. Anchored to the "Số:" line where
# possible so a number CITED from another document isn't mistaken for this
# document's own number. These are the only survivors of the old classifier.
_DOCNUM_PATTERNS = [
    re.compile(r'\b(\d+/\d{4}/QH\d+)\b', re.IGNORECASE),                  # law (QH)
    re.compile(r'(?im)^s[o?ố].{0,3}:\s*(\d+/\d{4}/TT-BGD.{1,2}T)\b'),     # circular
    re.compile(r'(?im)^s[o?ố].{0,3}:\s*(\d+\s*/\s*Q.{1,2}-.{0,3}HQG)\b'), # ĐHQG decision
    re.compile(r'(?im)^s[o?ố].{0,3}:\s*(\d+\s*/\s*Q.{1,2}-.{0,3}HQT)\b'), # ĐHQT decision
    re.compile(r'(?im)^s[o?ố].{0,3}:\s*(\d+[-\s]TB/DHQ.{0,2}-CTSV)\b'),   # announcement
]


def extract_doc_number(full_text: str) -> str:
    """Best-effort extraction of the document's OWN reference number.

    Independent of the group — purely a convenience for citation footers.
    Returns "" when no recognised number pattern is present.
    """
    head = full_text[:2000]
    for rx in _DOCNUM_PATTERNS:
        m = rx.search(head)
        if m:
            return m.group(1).strip()
    return ""


# ══════════════════════════════════════════════════════════════════════════════
#  Header normalisation
# ══════════════════════════════════════════════════════════════════════════════

_NORM_DIEU   = re.compile(r'(?im)^\s*[Dd][iIíị].{0,2}u\s+(\d+)')
_NORM_CHUONG = re.compile(r'(?im)^\s*[Cc]h[uưùúũụ].{0,3}ng\s+([IVXLCDM\d]+)')
_NORM_MUC    = re.compile(r'(?im)^\s*[Mm][uụùúũư].{0,1}c\s+([IVXLCDM\d]+)')
_NORM_KHOAN  = re.compile(r'(?im)^\s*[Kk]ho[ạaả].{0,1}n\s+(\d+)')


def _normalize_headers(text: str) -> str:
    """Replace garbled Vietnamese section headers with canonical forms."""
    text = _NORM_DIEU.sub(lambda m: f"Điều {m.group(1)}", text)
    text = _NORM_CHUONG.sub(lambda m: f"Chương {m.group(1)}", text)
    text = _NORM_MUC.sub(lambda m: f"Mục {m.group(1)}", text)
    text = _NORM_KHOAN.sub(lambda m: f"Khoản {m.group(1)}", text)
    return text


def _split_long_khoan(text: str, max_chars: int) -> list[str]:
    """Split a too-long Khoản at sentence boundaries, no overlap.

    Sentence boundary regex: a period (.!?) followed by whitespace, BUT the
    period must be preceded by a non-digit, non-whitespace character. This
    blocks splits at list markers like "1.", "2.", "12." — splitting there
    leaves a stub chunk containing just the marker (e.g. "1.") that pollutes
    retrieval. Splits at real sentence ends like "...trường." still work
    because the preceding char is a letter."""
    parts = re.split(r'(?<=[^\d\s][.!?])\s+', text)
    sentences = [p.strip() for p in parts if p.strip()]
    # Drop pure-numeric "sentences" — these are page numbers / footer artifacts
    # that PyMuPDF returns inline between sections. Without this filter they
    # leak into the last sub-chunk of a Khoản as a standalone "11" or "3".
    sentences = [s for s in sentences if not re.fullmatch(r'\d{1,3}\.?', s)]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for sent in sentences:
        slen = len(sent)
        if current_len + slen > max_chars and current:
            chunks.append(" ".join(current))
            current, current_len = [sent], slen
        else:
            current.append(sent)
            current_len += slen + 1
    if current:
        chunks.append(" ".join(current))
    return chunks


def _is_noise_chunk(text: str) -> bool:
    """True if the chunk has no retrievable content.

    Triggers on single-character OCR artifacts ('|', '†', 'E') and stray
    punctuation/digits that leaked through structural splits. A real
    short Khoản like "1. Các Khoa/Bộ môn." has ≥ 5 letters, so the
    threshold is safe.
    """
    if not text:
        return True
    letter_count = sum(1 for c in text if c.isalpha())
    return letter_count < 5


# ══════════════════════════════════════════════════════════════════════════════
#  System 2: Paragraph Chunker (Thông báo, Phụ lục, free-form)
# ══════════════════════════════════════════════════════════════════════════════

# Numbered list item: "1.", "2.", ... or "a)", "b)", ...
_RE_LIST_ITEM = re.compile(r'(?im)^\s*(?:\d{1,2}[\.\)]|[a-z]\))\s+')

MAX_PARAGRAPH_CHARS = 400
MIN_PARAGRAPH_CHARS = 100


def chunk_paragraph(full_text: str) -> list[dict]:
    """
    ParagraphChunker — for unstructured documents (announcements, appendices).

    Strategy:
      1. Split at paragraph boundaries (double newline).
      2. Inside each paragraph, split before numbered list items (1./2./a)/b)).
      3. Merge consecutive units forward while total stays < MAX_PARAGRAPH_CHARS
         AND current pending is < MIN_PARAGRAPH_CHARS.
      4. Sentence-split units longer than MAX_PARAGRAPH_CHARS, with a single
         trailing-sentence overlap between sub-chunks of the SAME paragraph.
    """
    full_text = re.sub(r'\n{3,}', '\n\n', full_text.strip())
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', full_text) if p.strip()]
    if not paragraphs:
        return []

    # Split numbered list items inside paragraphs into separate units
    units: list[str] = []
    for p in paragraphs:
        if _RE_LIST_ITEM.search(p):
            parts = re.split(r'(?im)(?=^\s*(?:\d{1,2}[\.\)]|[a-z]\))\s+)', p)
            units.extend(part.strip() for part in parts if part.strip())
        else:
            units.append(p)

    out: list[dict] = []

    def emit(text: str) -> None:
        text = text.strip()
        if text and not _is_noise_chunk(text):
            out.append({
                "section_type": "paragraph",
                "chapter": "", "chapter_title": "",
                "article": "", "article_title": "", "khoan": "",
                "text": text,
            })

    pending = ""
    for u in units:
        # Long unit — flush pending, then sentence-split with intra-overlap
        if len(u) > MAX_PARAGRAPH_CHARS:
            if pending:
                emit(pending)
                pending = ""
            subs = _split_long_khoan(u, MAX_PARAGRAPH_CHARS)
            prev_tail = ""
            for sub in subs:
                body = f"{prev_tail} {sub}".strip() if prev_tail else sub
                emit(body)
                prev_tail = _last_sentence(sub)
            continue

        # Short pending → keep accumulating
        if pending and len(pending) < MIN_PARAGRAPH_CHARS \
                and len(pending) + len(u) + 1 <= MAX_PARAGRAPH_CHARS:
            pending = f"{pending}\n{u}"
            continue

        # Otherwise flush pending and start fresh
        if pending:
            emit(pending)
        pending = u

    if pending:
        emit(pending)

    return out


# ══════════════════════════════════════════════════════════════════════════════
#  Post-processing: encoder budget and form detection
# ══════════════════════════════════════════════════════════════════════════════

# Vietnamese subword tokenisers emit roughly 1.6 tokens per whitespace word for
# the bi-encoder used here. Exactness is unnecessary — the point is to stay
# clear of the model's position limit, not to predict it.
TOKENS_PER_WORD = 1.6

# Form markers live in _PROFILE so a new corpus can supply its own; see
# configure(). Dot leaders are layout, not language, so they stay here.
_DOT_LEADER = re.compile(r"[.…]{4,}")


def approx_tokens(text: str) -> int:
    return int(len(text.split()) * TOKENS_PER_WORD)


def _is_form_text(text: str) -> bool:
    """True for form scaffolding rather than regulation.

    Appendices in these documents inherit whatever Điều/Khoản label preceded
    them, so a blank application form can be presented to the reader as a
    citable provision. Detecting the form lets the label be dropped instead.
    """
    if not text:
        return False
    low = text.lower()
    dotted = sum(len(m) for m in _DOT_LEADER.findall(text)) / max(len(text), 1)
    if dotted > 0.08:
        return True
    return sum(1 for m in _PROFILE["form_markers"] if m in low[:400]) >= 2


def _split_to_budget(text: str, max_tokens: int) -> list[str]:
    """Split text so each piece fits the encoder's window.

    Anything past the window is dropped silently at index time, so a long
    clause is effectively half-indexed and its tail can never be matched.
    Splitting here is safe for answers because the synthesis layer reassembles
    a clause from its consecutive pieces before quoting it.
    """
    if approx_tokens(text) <= max_tokens:
        return [text]

    max_words = max(int(max_tokens / TOKENS_PER_WORD), 30)
    pieces: list[str] = []
    current: list[str] = []
    count = 0

    for sentence in split_sentences_for_budget(text):
        words = len(sentence.split())
        if current and count + words > max_words:
            pieces.append(" ".join(current))
            current, count = [sentence], words
        else:
            current.append(sentence)
            count += words

    if current:
        pieces.append(" ".join(current))

    # A single sentence longer than the budget still has to be cut somewhere.
    out: list[str] = []
    for piece in pieces:
        words = piece.split()
        if len(words) <= max_words:
            out.append(piece)
        else:
            for i in range(0, len(words), max_words):
                out.append(" ".join(words[i:i + max_words]))

    out = [p for p in out if p.strip()]

    # Splitting on the budget tends to leave a remainder of a few words, and
    # such a fragment matches nothing on its own. Rebalance the final pair
    # instead of concatenating them: appending the tail would push its
    # predecessor back over the budget, which is the very truncation this
    # function exists to prevent.
    min_words = max(max_words // 5, 20)
    if len(out) > 1 and len(out[-1].split()) < min_words:
        merged = f"{out[-2]} {out[-1]}".split()
        if len(merged) <= max_words:
            out[-2:] = [" ".join(merged)]
        else:
            half = len(merged) // 2
            out[-2:] = [" ".join(merged[:half]), " ".join(merged[half:])]

    return out


_SENT_SPLIT_RE = re.compile(r"(?<=[.!?;:])\s+|\n+")


def split_sentences_for_budget(text: str) -> list[str]:
    parts = [p.strip() for p in _SENT_SPLIT_RE.split(text) if p.strip()]
    return parts or [text]


_HEADING_ONLY = re.compile(
    r"^\s*(ch[uư][oơ]ng\s+[IVXLCDM\d]+|m[uụ]c\s+[IVXLCDM\d]+|"
    r"[đd]i[eề]u\s+\d+|chapter\s+[IVXLCDM\d]+|article\s+\d+|"
    r"section\s+\d+|quy[eế]t\s+[đd][iị]nh\s*:)",
    re.IGNORECASE,
)

HEADING_MERGE_MAX_CHARS = 120


def _is_heading_only(text: str) -> bool:
    """A structural heading carrying no body text.

    "Điều 6. Trách nhiệm và quyền hạn của Trưởng Phòng CTSV" answers nothing on
    its own, so as a retrievable unit it is dead weight. As a prefix to the
    clauses beneath it, the same string is exactly the context the embedder
    needs to tell one Điều's clauses from another's.
    """
    stripped = text.strip()
    return bool(stripped) and len(stripped) <= HEADING_MERGE_MAX_CHARS and bool(
        _HEADING_ONLY.match(stripped)
    )


def _merge_headings_forward(chunks: list[dict]) -> list[dict]:
    """Attach heading-only chunks to the chunk that follows them."""
    out: list[dict] = []
    pending: list[str] = []

    for chunk in chunks:
        text = (chunk.get("text") or "").strip()
        if _is_heading_only(text):
            pending.append(text)
            continue
        if pending:
            chunk = dict(chunk)
            chunk["text"] = "\n".join(pending + [text])
            pending = []
        out.append(chunk)

    # A heading with nothing after it (document ends on one) has no body to
    # join, and is not worth indexing alone.
    return out


def postprocess_chunks(chunks: list[dict], max_tokens: int) -> list[dict]:
    """Drop bogus clause labels, attach headings, enforce the encoder budget."""
    chunks = _merge_headings_forward(chunks)

    out: list[dict] = []
    for chunk in chunks:
        text = chunk.get("text") or ""

        if _is_form_text(text):
            chunk = dict(chunk)
            chunk["section_type"] = "form"
            # The label was inherited from preceding body text, not earned.
            chunk["article"] = ""
            chunk["article_title"] = ""
            chunk["khoan"] = ""

        pieces = _split_to_budget(text, max_tokens)
        if len(pieces) == 1:
            out.append(chunk)
            continue
        for part_no, piece in enumerate(pieces):
            piece_chunk = dict(chunk)
            piece_chunk["text"] = piece
            piece_chunk["budget_part"] = part_no + 1
            piece_chunk["budget_parts"] = len(pieces)
            out.append(piece_chunk)
    return out


def _last_sentence(text: str) -> str:
    parts = re.split(r'(?<=[.!?])\s+', text.strip())
    return parts[-1] if parts else ""


# ══════════════════════════════════════════════════════════════════════════════
#  Pipeline orchestration
# ══════════════════════════════════════════════════════════════════════════════

def make_chunk_id(source: str, idx: int, text: str = "") -> str:
    """Stable identity for a chunk, derived from its content.

    Hashing position instead of content fails silently across a re-chunk: the
    id still resolves, but to different text. Measured on this corpus, a single
    re-parse left 99.8% of ground-truth references alive while 91.2% of them
    pointed at changed content — relevance judgments that look valid and are
    not, which is worse than a reference that plainly breaks.

    Hashing the text gives the property annotations need: unchanged content
    keeps its id and its judgments, changed content gets a new id and drops out
    visibly. Source and index stay in the hash so two chunks that happen to
    share wording (identical form fields, repeated table rows) remain distinct.
    """
    normalised = " ".join((text or "").split())
    raw = f"{source}::{idx}::{normalised}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]


def _build_page_index(pages: list[dict]) -> list[tuple[int, int, int]]:
    """Build list of (char_start, char_end, page_number) for full-text offset lookup."""
    index: list[tuple[int, int, int]] = []
    offset = 0
    for p in pages:
        end = offset + len(p["text"])
        index.append((offset, end, p["page"]))
        offset = end + 2  # accounts for "\n\n" separator
    return index


def _find_page(char_pos: int, page_index: list[tuple[int, int, int]], fallback: int) -> int:
    page = fallback
    for start, end, pg in page_index:
        if char_pos >= start:
            page = pg
        else:
            break
    return page


# ════════════════════════════════════════════════════════════════════════════
# HEADING_CATALOG — single source of truth for structural detection
# ════════════════════════════════════════════════════════════════════════════
#
# Each row: (label, regex_string, default_level)
# Level convention:
#   1 = top-level section (Chương / Chapter / Part / named heading)
#   2 = mid-level         (Điều / Article / Section / §)
#   3 = leaf              (Khoản / numbered list / lettered sublist)
#
# Patterns are tried INDEPENDENTLY; the winner per level is the one with
# the most DISTINCT group(1) values (≥ MIN_DISTINCT).
#
# Adding new formats = adding a regex row. No other code changes needed.

HEADING_CATALOG: list[tuple[str, str, int]] = [
    # ─── Level 1 ────────────────────────────────────────────────────────
    ("CHUONG_VN",      r'(?im)^\s*ch[uư]{1,2}[ơo]?ng\s+([IVXLCDM\d]+)',  1),
    ("CHAPTER_EN",     r'(?im)^\s*chapter\s+(\d+|[IVXLCDM]+)\b',           1),
    ("PHAN_VN",        r'(?im)^\s*ph[aâầ]n\s+([IVXLCDM\d]+)\b',            1),
    ("PART_EN",        r'(?im)^\s*part\s+([IVXLCDM\d]+)\b',                1),
    ("ROMAN_TITLE",    r'(?im)^\s*([IVXLCDM]{1,4})\.\s+\S',                1),
    ("NAMED_VN",       r'(?im)^\s*(MỞ ĐẦU|TỔNG QUAN|ĐẶT VẤN ĐỀ|TÓM TẮT'
                       r'|KẾT LUẬN|KIẾN NGHỊ|TÀI LIỆU THAM KHẢO|PHỤ LỤC'
                       r'|MỤC LỤC|DANH MỤC (?:HÌNH|BẢNG|VIẾT TẮT|KÝ HIỆU'
                       r'|CHỮ VIẾT TẮT)|LỜI (?:CAM ĐOAN|CẢM ƠN|NÓI ĐẦU))'
                       r'\s*$',                                             1),
    ("NAMED_EN",       r'(?im)^\s*(ABSTRACT|INTRODUCTION|METHODOLOGY'
                       r'|RESULTS|DISCUSSION|CONCLUSION|REFERENCES'
                       r'|APPENDIX)\s*$',                                   1),
    # ─── Level 2 ────────────────────────────────────────────────────────
    ("DIEU_VN",        r'(?im)^\s*[đdĐD]i[eêề]u\s+(\d+)\b',                 2),
    ("ARTICLE_EN",     r'(?im)^\s*article\s+(\d+)\b',                      2),
    ("SECTION_EN",     r'(?im)^\s*section\s+(\d+(?:\.\d+)?)\b',            2),
    ("PARAGRAPH_SIGN", r'(?im)^\s*§\s*(\d+)',                              2),
    ("MUC_VN",         r'(?im)^\s*m[uụ]c\s+([IVXLCDM\d]+)\b',              2),
    # ─── Level 3 ────────────────────────────────────────────────────────
    ("KHOAN_VN",       r'(?im)^\s*kho[ạaả]n\s+(\d+)\b',                    3),
    ("NUM_DOT",        r'(?im)^\s*(\d+)\.\s+(?=[A-ZĐÁÀẢÃẠĂÂẦẤẨẪẬÉÈẺẼẸÊẾỀỂỄỆÍÌỈĨỊÔƠÚÙỦŨỤƯÝỲỶỸỴ])', 3),
    ("NUM_DOT_NUM",    r'(?im)^\s*(\d+\.\d+(?:\.\d+)*)\s+',                3),
    ("LETTER_LIST",    r'(?im)^\s*([a-z])[\.\)]\s+(?=\S)',                 3),
    ("ROMAN_LOWER",    r'(?im)^\s*([ivxlcdm]{1,5})\.\s+(?=[A-ZĐÁÀ])',      3),
    ("PAREN_DIGIT",    r'(?im)^\s*\((\d+)\)\s+(?=\S)',                     3),
    ("THEOREM_STYLE",  r'(?m)^\s*(Định nghĩa|Định lý|Bổ đề|Hệ quả'
                       r'|Mệnh đề|Ví dụ|Nhận xét|Chứng minh|Bài toán)'
                       r'\s*(\d+(?:\.\d+)*)?[\.\:]',                       3),
    ("CAU_BAI",        r'(?im)^\s*(?:câu|bài|vấn đề)\s+(\d+(?:\.\d+)*)'
                       r'\s*[\.:]',                                        3),
]

_COMPILED_CATALOG = [(lbl, re.compile(pat), lvl) for lbl, pat, lvl in HEADING_CATALOG]

MIN_DISTINCT = 3        # threshold to qualify as "real heading pattern"
MAX_LEAF = 512          # leaf chunks larger than this → sentence-split
MIN_LEAF = 50           # leaves smaller than this → merge forward


# ════════════════════════════════════════════════════════════════════════════
# Phase 1: Detect dominant heading pattern per level
# ════════════════════════════════════════════════════════════════════════════

def detect_pattern(full_text: str) -> dict[int, tuple[str, re.Pattern]]:
    """Pick winning pattern per level.

    Returns {level: (label, compiled_regex)} for each level that has a
    qualifying pattern. Missing levels are omitted from the dict.
    """
    scored: dict[int, list[tuple[int, str, re.Pattern]]] = defaultdict(list)
    for label, regex, lvl in _COMPILED_CATALOG:
        distinct = set()
        for m in regex.finditer(full_text):
            try:
                distinct.add(m.group(1))
            except IndexError:
                distinct.add(m.group(0).strip())
        if len(distinct) >= MIN_DISTINCT:
            scored[lvl].append((len(distinct), label, regex))

    winners: dict[int, tuple[str, re.Pattern]] = {}
    for lvl, opts in scored.items():
        opts.sort(reverse=True)            # most-distinct wins
        n, label, regex = opts[0]
        winners[lvl] = (label, regex)
    return winners


# ════════════════════════════════════════════════════════════════════════════
# Phase 2: Hierarchical split using detected patterns
# ════════════════════════════════════════════════════════════════════════════

def _split_at(text: str, regex: re.Pattern) -> list[tuple[str, str]]:
    """Split text at each regex match (lookbehind-style).

    Returns list of (label, section_text). label is the raw match group(1)
    (or group(0) for keyword-only patterns). The first segment before any
    match — if non-empty — is returned with label="" as preamble.
    """
    matches = list(regex.finditer(text))
    if not matches:
        return [("", text)]
    segments: list[tuple[str, str]] = []
    # Preamble (before first match)
    if matches[0].start() > 0:
        pre = text[:matches[0].start()].strip()
        if pre:
            segments.append(("", pre))
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        try:
            label = m.group(1).strip()
        except IndexError:
            label = m.group(0).strip()
        segments.append((label, text[start:end].strip()))
    return segments


def _enforce_size(text: str, max_chars: int = MAX_LEAF) -> list[str]:
    """Sentence-split text exceeding max_chars; otherwise return [text]."""
    text = text.strip()
    if len(text) <= max_chars:
        return [text]
    return _split_long_khoan(text, max_chars)


def hierarchical_split(
    full_text: str,
    winners: dict[int, tuple[str, re.Pattern]],
) -> list[dict]:
    """Apply detected heading patterns to split full_text into chunks.

    Returns list of chunk dicts with keys:
      section_type, chapter, chapter_title, article, article_title, khoan, text

    For backward compatibility with rag_chain.py, the following mapping is
    applied:
       L1 label → chapter            (raw group value)
       L2 label → article            (raw group value)
       L3 label → khoan              (raw group value)
       Section title (line remainder after the heading) → *_title fields
    """
    if not winners:
        # No structural pattern detected → paragraph chunker.
        return chunk_paragraph(full_text)

    r1 = winners.get(1, (None, None))[1]
    r2 = winners.get(2, (None, None))[1]
    r3 = winners.get(3, (None, None))[1]

    # Level 1 split
    sections_l1 = _split_at(full_text, r1) if r1 else [("", full_text)]

    results: list[dict] = []
    for l1_label, l1_text in sections_l1:
        l1_title = _heading_title(l1_text, r1) if (r1 and l1_label) else ""

        # Level 2 split inside this L1 section
        sections_l2 = _split_at(l1_text, r2) if r2 else [("", l1_text)]
        for l2_label, l2_text in sections_l2:
            l2_title = _heading_title(l2_text, r2) if (r2 and l2_label) else ""

            # Level 3 split inside this L2 section
            sections_l3 = _split_at(l2_text, r3) if r3 else [("", l2_text)]

            # Determine section_type for downstream tier classifier:
            #   L3 leaf → "khoan"     (when l3 has a label)
            #   L2 leaf → "article"   (when only l2 label present)
            #   L1 leaf → "muc"       (when only l1 label, no l2/l3)
            #   else    → "text"      (preamble before first heading)

            pending_for_merge: dict | None = None
            for l3_label, l3_text in sections_l3:
                # Decide section_type & metadata
                if l3_label:
                    sec_type = "khoan"
                elif l2_label:
                    sec_type = "article"
                elif l1_label:
                    sec_type = "muc"
                else:
                    sec_type = "text"

                chunk_meta = {
                    "section_type":   sec_type,
                    "chapter":        l1_label,
                    "chapter_title":  l1_title,
                    "article":        l2_label,
                    "article_title":  l2_title,
                    "khoan":          l3_label,
                }

                # Enforce max size (sentence-split) and min size (merge forward)
                for sub_text in _enforce_size(l3_text):
                    if _is_noise_chunk(sub_text):
                        continue
                    candidate = {**chunk_meta, "text": sub_text}

                    if (pending_for_merge
                            and len(pending_for_merge["text"]) < MIN_LEAF
                            and pending_for_merge["chapter"] == candidate["chapter"]
                            and pending_for_merge["article"] == candidate["article"]):
                        # Merge tiny leaf forward (preserve lower-number khoan)
                        pending_for_merge["text"] = (
                            f"{pending_for_merge['text']}\n{candidate['text']}".strip()
                        )
                        continue

                    if pending_for_merge:
                        results.append(pending_for_merge)
                    pending_for_merge = candidate

            if pending_for_merge:
                results.append(pending_for_merge)

    # Drop pure-noise residuals
    return [r for r in results if not _is_noise_chunk(r["text"])]


def _heading_title(section_text: str, regex: re.Pattern) -> str:
    """Extract the remainder of the heading line after the match.

    Example: section_text starts with "Điều 5. Quy định về thi cử\n1. ..."
    regex matches "Điều 5" → title returned = "Quy định về thi cử"
    """
    m = regex.search(section_text)
    if not m:
        return ""
    line_end = section_text.find("\n", m.end())
    if line_end == -1:
        line_end = len(section_text)
    tail = section_text[m.end():line_end].strip()
    # Strip leading punctuation like "." or ":"
    return re.sub(r'^[\.\:\-–—\s]+', '', tail).strip()[:200]


# ════════════════════════════════════════════════════════════════════════════
# Pipeline orchestration
# ════════════════════════════════════════════════════════════════════════════

def extract_pdf(pdf_path: Path, ocr_log: list[int] | None = None) -> tuple[list[dict], list[dict]]:
    """Text pages and table rows of a PDF (unchanged extraction path)."""
    # Tables first → so their bboxes can redact the text region.
    table_chunks, table_bboxes = extract_tables(pdf_path)

    # OCR-aware page text extraction with table region redaction.
    pages = extract_pages(pdf_path, exclude_bboxes=table_bboxes, ocr_log=ocr_log)
    return pages, table_chunks


def _replace_file(src: Path, dst: Path, attempts: int = 10) -> None:
    """os.replace, retried: on Windows a reader holding `dst` open (the API
    listing chunk files) makes the replace fail for as long as it reads."""
    for attempt in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.2)


def _chunk_and_write(
    pages: list[dict],
    table_chunks: list[dict],
    *,
    source_name: str,
    source_path: str,
    out_path: Path,
    group: str,
    registry: dict,
    max_tokens: int,
    metadata_overrides: dict | None = None,
    extra_fields: dict | None = None,
    doc_number_text: str | None = None,
    heading_blocks: list | None = None,
    page_fallback: str = "first",
) -> tuple[int, str, dict]:
    """Chunk extracted pages + table rows and write one JSONL file.

    Shared by every format; for a PDF with no sidecar it does exactly what
    process_pdf always did, field for field. Returns (chunks, label, report).

    extra_fields     appended to every record (documents uploaded through the
                     ingestion flow: file_type, language, ingest_version)
    doc_number_text  where to look for the reference number (DOCX: the whole
                     document incl. tables, since it often sits in a letterhead table)
    heading_blocks   DOCX paragraphs with outline levels, for documents that
                     structure themselves with Word headings instead of numbering
    page_fallback    "first" (PDF, unchanged) or "previous" chunk's page
    """
    report = {"chunker": "none", "chunks_text": 0, "chunks_table": 0, "warnings": []}
    if not pages and not table_chunks:
        return 0, "none", report

    full_text = "\n\n".join(p["text"] for p in pages) if pages else ""
    # CRITICAL: normalize before pattern detection so OCR'd "Dieu 5" → "Điều 5".
    if full_text and _PROFILE.get("vi_normalization", True):
        full_text = _normalize_headers(full_text)

    # Document metadata: group/type/issuing_body come from the folder + registry
    # (no content guessing); only the reference number is read from the body.
    # A value set by the user at upload (sidecar) takes precedence.
    gm = group_meta(group, registry)
    overrides = metadata_overrides or {}
    number_text = full_text if doc_number_text is None else doc_number_text
    doc_meta = {
        "doc_group":    gm["doc_group"],
        "doc_type":     overrides.get("doc_type") or gm["doc_type"],
        "doc_number":   overrides.get("doc_number") or (extract_doc_number(number_text) if number_text else ""),
        "issuing_body": overrides.get("issuing_body") or gm["issuing_body"],
    }

    page_index = _build_page_index(pages) if pages else []
    winners = detect_pattern(full_text) if full_text else {}
    level_labels = {"chapter": "", "article": "", "khoan": ""}

    # Build chunker label for reporting
    headings = [b for b in (heading_blocks or []) if b.kind == "heading" and b.level is not None and b.level <= 2]
    if heading_blocks is not None and full_text and not (winners.get(1) or winners.get(2)) and len(headings) >= 3:
        # A Word document without Chương/Điều/Section numbering, organised by
        # its own heading styles: split along those instead.
        chunks = heading_split(heading_blocks)
        used = sorted({b.level for b in headings})
        chunker = "heading(" + "+".join(f"H{lvl + 1}" for lvl in used) + ")"
        level_labels = {"chapter": "HEADING_1", "article": "HEADING_2", "khoan": "HEADING_3"}
    elif full_text and winners:
        levels_used = "+".join(
            f"L{lvl}:{lbl}" for lvl, (lbl, _) in sorted(winners.items())
        )
        chunks = hierarchical_split(full_text, winners)
        chunker = f"hpad({levels_used})"
        for lvl, key in ((1, "chapter"), (2, "article"), (3, "khoan")):
            if lvl in winners:
                level_labels[key] = winners[lvl][0]
    elif full_text:
        chunks = chunk_paragraph(full_text)
        chunker = "paragraph"
    else:
        chunks = []
        chunker = "table_only"

    # Strip inherited clause labels from forms, and cut anything the encoder
    # would silently truncate at index time.
    chunks = postprocess_chunks(chunks, max_tokens)

    # Table rows face the same encoder limit — a wide row with long cells
    # overruns it just as a long clause does. Heading merging and form
    # detection do not apply to them, so only the budget is enforced.
    table_chunks = [
        {**tc, "text": piece}
        for tc in table_chunks
        for piece in _split_to_budget(tc.get("text") or "", max_tokens)
    ]

    fallback_page = (
        pages[0]["page"] if pages
        else (table_chunks[0]["page"] if table_chunks else 1)
    )

    extra = dict(extra_fields or {})
    if extra:
        extra["level_labels"] = level_labels

    # Written beside the target and swapped in, so a crash mid-write never
    # leaves a truncated chunk file for the indexers to choke on.
    tmp_path = out_path.with_name(out_path.name + ".tmp")
    previous_page = fallback_page
    with tmp_path.open("w", encoding="utf-8") as f:
        for idx, chunk in enumerate(chunks):
            probe = chunk["text"][:60]

            char_pos = full_text.find(probe) if full_text else -1
            if char_pos >= 0:
                page_num = _find_page(char_pos, page_index, fallback_page)
            else:
                page_num = previous_page if page_fallback == "previous" else fallback_page
            previous_page = page_num
            record = {
                "chunk_id":      make_chunk_id(source_name, idx, chunk["text"]),
                "source":        source_name,
                "source_path":   source_path,
                "page":          page_num,
                "chunk_index":   idx,
                "text":          chunk["text"],
                # Document-level metadata from the reused v1 classifier.
                "doc_group":     doc_meta["doc_group"],
                "doc_type":      doc_meta["doc_type"],
                "doc_number":    doc_meta["doc_number"],
                "issuing_body":  doc_meta["issuing_body"],
                # Structural metadata mapped from L1/L2/L3 detection
                "section_type":  chunk.get("section_type", "text"),
                "chapter":       chunk.get("chapter", ""),
                "chapter_title": chunk.get("chapter_title", ""),
                "article":       chunk.get("article", ""),
                "article_title": chunk.get("article_title", ""),
                "khoan":         chunk.get("khoan", ""),
            }
            record.update(extra)
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        # Table-row chunks: indices continue after text chunks
        for offset, tc in enumerate(table_chunks):
            idx = len(chunks) + offset
            record = {
                "chunk_id":      make_chunk_id(source_name, idx, tc["text"]),
                "source":        source_name,
                "source_path":   source_path,
                "page":          tc["page"],
                "chunk_index":   idx,
                "text":          tc["text"],
                "doc_group":     doc_meta["doc_group"],
                "doc_type":      doc_meta["doc_type"],
                "doc_number":    doc_meta["doc_number"],
                "issuing_body":  doc_meta["issuing_body"],
                "section_type":  "table_row",
                "chapter":       "",
                "chapter_title": "",
                "article":       "",
                "article_title": "",
                "khoan":         "",
            }
            record.update(extra)
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    _replace_file(tmp_path, out_path)

    total = len(chunks) + len(table_chunks)
    label = chunker + (f"+{len(table_chunks)}tbl" if table_chunks else "")
    report.update({"chunker": label, "chunks_text": len(chunks), "chunks_table": len(table_chunks)})
    return total, label, report


def process_pdf(pdf_path: Path, out_dir: Path,
                group: str = "", registry: dict | None = None,
                max_tokens: int = 256) -> tuple[int, str]:
    """Process a single PDF. Returns (num_chunks, chunker_label).

    `group` is the data/<group>/ folder the file lives in (the user's choice);
    `registry` is the loaded data/groups.json mapping. Together they supply the
    document-level metadata that the old content classifier used to guess.
    """
    pages, table_chunks = extract_pdf(pdf_path)
    total, label, _ = _chunk_and_write(
        pages, table_chunks,
        source_name=pdf_path.name, source_path=str(pdf_path),
        out_path=out_dir / (pdf_path.stem + ".jsonl"),
        group=group, registry=registry or {}, max_tokens=max_tokens,
    )
    return total, label


# ════════════════════════════════════════════════════════════════════════════
# DOCX and heading-structured documents
# ════════════════════════════════════════════════════════════════════════════

_LEADING_NUMBER = re.compile(r"^\s*((?:\d+|[IVXLCDM]+)(?:\.\d+)*)[.):]?\s+(?=\S)")


def heading_split(blocks: list) -> list[dict]:
    """Chunk a document along its Word heading levels (H1/H2/H3).

    For documents with no numbering pattern the heading catalog recognises —
    reports, policies, manuals — the author's own headings are the structure.
    They map onto the same three fields HPAD fills, so citation, the tier
    classifier and clause assembly treat both alike: H1 → chapter,
    H2 → article, H3 → khoan (value = the heading's leading number, else its
    ordinal; title = the heading text). Body text under a heading is chunked
    with the paragraph chunker and the heading is prefixed to its first chunk.
    """
    labels: dict[int, tuple[str, str]] = {0: ("", ""), 1: ("", ""), 2: ("", "")}
    ordinals = [0, 0, 0]

    def meta() -> dict:
        ch, art, kh = labels[0], labels[1], labels[2]
        section_type = "khoan" if kh[0] else "article" if art[0] else "muc" if ch[0] else "text"
        return {"section_type": section_type, "chapter": ch[0], "chapter_title": ch[1],
                "article": art[0], "article_title": art[1], "khoan": kh[0]}

    sections: list[dict] = [{"meta": meta(), "heading": "", "body": []}]
    for b in blocks:
        if b.kind == "heading" and b.level is not None and b.level <= 2:
            lvl = b.level
            ordinals[lvl] += 1
            for deeper in range(lvl + 1, 3):
                ordinals[deeper] = 0
                labels[deeper] = ("", "")
            m = _LEADING_NUMBER.match(b.text)
            value = m.group(1) if m else str(ordinals[lvl])
            title = (b.text[m.end():] if m else b.text).strip()
            labels[lvl] = (value, title[:200])
            sections.append({"meta": meta(), "heading": b.text.strip(), "body": []})
        else:
            sections[-1]["body"].append(b.text)

    out: list[dict] = []
    carry: list[str] = []          # headings with no body of their own
    for sec in sections:
        pieces = chunk_paragraph("\n\n".join(sec["body"])) if sec["body"] else []
        if sec["heading"]:
            carry.append(sec["heading"])
        if not pieces:
            continue
        pieces[0]["text"] = "\n".join(carry + [pieces[0]["text"]])
        carry = []
        for piece in pieces:
            piece.update(sec["meta"])
            out.append(piece)
    # A document that ends on a heading has no body to attach it to.
    return out


def _import_docx_extractor():
    try:
        from src.ingest.docx_extract import extract_docx
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from src.ingest.docx_extract import extract_docx
    return extract_docx


def process_document(
    path: Path,
    out_dir: Path,
    group: str = "",
    registry: dict | None = None,
    max_tokens: int = 256,
    sidecar: dict | None = None,
    cfg: dict | None = None,
) -> tuple[int, str, dict]:
    """Parse one PDF or DOCX into <stem>.jsonl. Returns (chunks, label, report).

    `sidecar` is the document's upload metadata (language, metadata set by the
    user). Without one — every document of the original corpus — a PDF goes
    through exactly the process_pdf path and its records are unchanged.
    """
    registry = registry or {}
    ext = path.suffix.lower()
    language = (sidecar or {}).get("language") or None
    overrides = ((sidecar or {}).get("metadata") or {}) if sidecar else {}
    extra = None
    if sidecar is not None:
        extra = {"file_type": ext.lstrip("."), "language": language or "vi", "ingest_version": 2}
    out_path = out_dir / (path.stem + ".jsonl")
    common = dict(source_name=path.name, source_path=str(path), out_path=out_path,
                  group=group, registry=registry, max_tokens=max_tokens,
                  metadata_overrides=overrides, extra_fields=extra)

    with language_profile(profile_overrides(language, cfg)):
        if ext == ".pdf":
            ocr_pages: list[int] = []
            pages, table_chunks = extract_pdf(path, ocr_log=ocr_pages)
            total, label, report = _chunk_and_write(pages, table_chunks, **common)
            try:
                doc = fitz.open(str(path))
                report["pages"] = doc.page_count
                doc.close()
            except Exception:
                report["pages"] = len(pages)
            report["ocr_pages"] = ocr_pages
        elif ext == ".docx":
            extract_docx = _import_docx_extractor()
            ex = extract_docx(
                path,
                caption_ok=_usable_caption,
                header_candidate=_is_header_candidate,
                build_labels=_build_column_labels,
            )
            total, label, report = _chunk_and_write(
                ex.pages, ex.table_chunks, **common,
                doc_number_text=ex.linear_text, heading_blocks=ex.blocks,
                page_fallback="previous",
            )
            report["pages"] = ex.page_count
            report["ocr_pages"] = []
            report["warnings"] = report.get("warnings", []) + ex.warnings
        else:
            raise ValueError(f"unsupported document type: {path.name}")
    return total, label, report


def read_sidecar(path: Path) -> dict | None:
    """The upload sidecar next to a document (<name>.<ext>.meta.json), if any."""
    side = path.with_name(path.name + ".meta.json")
    try:
        with side.open(encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def list_documents(raw_dir: Path, skip_dirs: set[str]) -> tuple[list[Path], list[Path]]:
    """(PDFs, DOCX files) under raw_dir that belong to a document group."""
    def keep(p: Path) -> bool:
        parts = p.relative_to(raw_dir).parts
        if set(parts) & skip_dirs:
            return False
        # Internal folders (processed backups, ingestion state) are not groups.
        if any(part.startswith((".", "_")) or part.lower().startswith("processed") for part in parts[:-1]):
            return False
        return not p.name.startswith("~$")      # Word's lock file for an open .docx

    pdfs = [p for p in raw_dir.rglob("*.pdf") if keep(p)]
    docxs = [p for p in raw_dir.rglob("*.docx") if keep(p)]
    return pdfs, docxs


def prune_orphans(out_dir: Path, documents: list[Path]) -> list[str]:
    """Delete chunk files whose source document no longer exists.

    Without this a deleted document stays searchable forever: nothing else
    ever removes its <stem>.jsonl, and every indexer reads all of them.
    """
    stems = {p.stem.casefold() for p in documents}
    removed = []
    for jl in sorted(out_dir.glob("*.jsonl")):
        if jl.stem.casefold() not in stems:
            jl.unlink()
            removed.append(jl.name)
    return removed


def run(config_path: str = "config.yaml",
        out_dir_override: str | None = None,
        only_files: list[str] | None = None,
        prune: bool = False):
    cfg = load_config(config_path)
    raw_dir = Path(cfg["data"]["raw_dir"])
    # Honour the config's processed_dir so the downstream stages
    # (02_embed_index, 03_bm25_index) read from the same place this writes to.
    # --out-dir stays available as an explicit override (e.g. dry-run compares).
    out_dir = Path(out_dir_override or cfg["data"]["processed_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    # The registry sits beside the documents it describes — data/groups.json
    # under the default config, the same file as before.
    registry = load_group_registry(raw_dir / "groups.json")
    configure(cfg)

    # Chunks longer than the encoder's window are truncated at index time, so
    # the ceiling belongs to whichever model does the embedding. Kept in config
    # rather than hardcoded: swapping the encoder should not need a code edit.
    max_tokens = int(cfg.get("chunking", {}).get("max_tokens", 256))

    # Skip the processed-output dirs in case they live under raw_dir (they hold
    # JSONL, not PDFs, but guard anyway against future layout changes).
    _skip_dirs = {Path(cfg["data"]["processed_dir"]).name, "processed", "processed_v2"}
    all_pdfs, all_docx = list_documents(raw_dir, _skip_dirs)
    all_docs = all_pdfs + all_docx
    if only_files:
        wanted = {Path(f).name for f in only_files}
        doc_files = [p for p in all_docs if p.name in wanted]
    else:
        doc_files = all_docs

    # Two documents sharing a stem would write the same <stem>.jsonl, and the
    # second would silently replace the first in every index. The first one
    # found keeps its place; the other is reported instead of parsed.
    owner: dict[str, Path] = {}
    stem_conflict: dict[Path, Path] = {}
    for p in all_docs:
        key = p.stem.casefold()
        if key in owner:
            stem_conflict[p] = owner[key]
        else:
            owner[key] = p

    def _group_of(pdf: Path) -> str:
        """Group = the first folder under raw_dir; '' if the file sits at root."""
        rel = pdf.relative_to(raw_dir)
        return rel.parts[0] if len(rel.parts) > 1 else ""

    n_docx = sum(1 for p in doc_files if p.suffix.lower() == ".docx")
    if n_docx:
        print(f"HPAD chunker — processing {len(doc_files) - n_docx} PDFs + {n_docx} DOCX → {out_dir}")
    else:
        print(f"HPAD chunker — processing {len(doc_files)} PDFs → {out_dir}")

    total_chunks = 0
    hpad_count = 0
    paragraph_count = 0
    table_only_count = 0
    heading_count = 0
    skipped: list[str] = []
    level_dist: dict[str, int] = defaultdict(int)

    for pdf in doc_files:
        if pdf in stem_conflict:
            print(f"  [ERROR] {pdf.name}: STEM_CONFLICT with {stem_conflict[pdf].name}")
            skipped.append(pdf.name)
            continue
        try:
            n, chunker, _ = process_document(pdf, out_dir, _group_of(pdf), registry,
                                             max_tokens=max_tokens,
                                             sidecar=read_sidecar(pdf), cfg=cfg)
        except Exception as exc:
            print(f"  [ERROR] {pdf.name}: {exc}")
            skipped.append(pdf.name)
            continue

        if n == 0:
            print(f"  [EMPTY] {pdf.name}")
            skipped.append(pdf.name)
            continue

        base = chunker.split("+")[0] if "+" in chunker else chunker
        if base.startswith("hpad"):
            tag = "HPAD "
            hpad_count += 1
            # Track which level combos appear
            inner = base[base.find("(") + 1: base.rfind(")")]
            level_dist[inner] += 1
        elif base == "paragraph":
            tag = "PARA "
            paragraph_count += 1
        elif base == "table_only":
            tag = "TABLE"
            table_only_count += 1
        elif base.startswith("heading"):
            tag = "HEAD "
            heading_count += 1
        else:
            tag = "?    "
        print(f"  [{tag}] {pdf.name}: {n} chunks ({chunker})")
        total_chunks += n

    print(f"\n══ Summary ══")
    print(f"  Total chunks       : {total_chunks}")
    print(f"  HPAD chunker       : {hpad_count} file(s)")
    if heading_count:
        print(f"  Heading chunker    : {heading_count} file(s)")
    print(f"  Paragraph chunker  : {paragraph_count} file(s)")
    print(f"  Table-only         : {table_only_count} file(s)")
    print(f"  Skipped (0 chunks) : {len(skipped)} file(s)")
    if level_dist:
        print(f"\n  Level pattern distribution:")
        for combo, cnt in sorted(level_dist.items(), key=lambda x: -x[1]):
            print(f"    {cnt:>2}× {combo}")
    if skipped:
        print(f"\n  Skipped files:")
        for name in skipped:
            print(f"    • {name}")
    if prune:
        removed = prune_orphans(out_dir, all_docs)
        print(f"\n  Pruned orphan chunk files: {len(removed)}")
        for name in removed:
            print(f"    • {name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Parse & chunk PDF/DOCX documents (HPAD)")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--out-dir", default=None,
                        help="Output dir override (default: config's processed_dir)")
    parser.add_argument("--only-files", nargs="+", default=None)
    parser.add_argument("--prune", action="store_true",
                        help="Delete <stem>.jsonl files whose document no longer exists "
                             "(a deleted document otherwise stays in every index).")
    args = parser.parse_args()
    run(args.config, args.out_dir, args.only_files, prune=args.prune)
