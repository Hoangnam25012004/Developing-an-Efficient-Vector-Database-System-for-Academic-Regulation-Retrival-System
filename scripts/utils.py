import os
import re
import unicodedata
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import regex as re2
import ujson
import yaml
from unidecode import unidecode

# -------------------------
# Optional dependencies
# -------------------------
try:
    import fitz  # PyMuPDF
except ModuleNotFoundError:
    fitz = None

try:
    from PIL import Image
except ModuleNotFoundError:
    Image = None

try:
    import pytesseract
except ModuleNotFoundError:
    pytesseract = None

try:
    import io
except ModuleNotFoundError:
    io = None

DEFAULT_TESSERACT_CMD = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# -------------------------
# Tokenization
# -------------------------
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


def vnfold(s: str) -> str:
    return unidecode(s or "").lower()

def fold_for_match(s: str) -> str:
    """
    Dùng cho match marker như 'Điều/Khoản/Chương/Phần' (không cần phân biệt đ).
    """
    return vnfold(s or "").strip()

def lower_keep_vietnamese(s: str) -> str:
    """
    Lowercase nhưng giữ ký tự tiếng Việt (đừng unidecode) để phân biệt d/đ.
    """
    return (s or "").strip().lower()


# -------------------------
# Unicode normalisation helpers (NEW)
# -------------------------

def normalize_vietnamese(text: str) -> str:
    """NFC-normalise và loại bỏ ký tự vô hình thường gặp trong PDF tiếng Việt.

    Nhiều font PDF lưu ký tự tiền hợp (precomposed) dưới dạng chuỗi phân giải
    (NFD).  Chuẩn hóa về NFC đảm bảo so sánh chuỗi và tokenize chính xác.

    Đồng thời xóa:
    * Zero-width space / joiner / BOM / soft-hyphen (U+200B–U+200D, U+FEFF, U+00AD)
      thường xuất hiện trong PDF do phần mềm xuất file cài thêm.
    * Ký tự thay thế U+FFFD (thường từ watermark / stamp bị OCR đọc sai font).
    """
    text = unicodedata.normalize("NFC", text or "")
    text = re.sub(r"[\u200b\u200c\u200d\ufeff\u00ad]", "", text)
    text = text.replace("\ufffd", "")
    return text


def _vietnamese_char_ratio(text: str) -> float:
    """Tỷ lệ ký tự có dấu tiếng Việt trong văn bản.

    Văn bản tiếng Việt bình thường có ~5–15 % ký tự thuộc các khối
    Latin Extended (U+00C0–U+024F) và Latin Extended Additional (U+1E00–U+1EFF)
    — hai khối chứa toàn bộ ký tự tiền hợp tiếng Việt.

    Tỷ lệ gần 0 trên văn bản dài (> 300 ký tự) gần như chắc chắn là dấu hiệu
    font PDF bị mã hóa sai: ký tự Unicode bị ánh xạ nhầm sang ASCII thuần
    (e.g. "DAr HQC" thay vì "ĐẠI HỌC").
    """
    if not text:
        return 0.0
    vi = sum(
        1 for c in text
        if 0x00C0 <= ord(c) <= 0x024F or 0x1E00 <= ord(c) <= 0x1EFF
    )
    return vi / len(text)


def force_item_newlines(text: str) -> str:
    """
    Ép xuống dòng trước các mục "12. ..." khi nó bị dính vào câu trước.
    Hữu ích cho PDF dạng bảng (non-OCR) để ITEM_RE bắt được.
    """
    s = (text or "")

    # xuống dòng trước " 23. " nếu trước đó không phải newline
    s = re2.sub(r"(?<!\n)\s+(\d{1,3})\.\s+", r"\n\1. ", s)

    # chuẩn hoá nhiều newline
    s = re2.sub(r"\n{3,}", "\n\n", s)
    return s.strip()

def force_decimal_item_newlines(text: str) -> str:
    """
    Ép xuống dòng trước các mục dạng 1.1 / 1.1.1 / 2.2.7 ... khi nó bị dính trong 1 dòng.
    Rất quan trọng cho PDF dạng bảng/phụ lục.
    """
    s = text or ""
    # "... +20 1.1.2 80 ≤ ..." -> "\n1.1.2 80 ≤ ..."
    s = re2.sub(
        r"(?<!\n)\s+((?:[1-9]\d?)(?:\.\d{1,2}){1,3})\s+",
        r"\n\1 ",
        s,
    )
    s = re2.sub(r"\n{3,}", "\n\n", s)
    return s.strip()

def is_table_like(lines: List[str]) -> bool:
    """
    Heuristic nhận diện PDF dạng bảng phụ lục kỷ luật:
    - nhiều keyword "Lần 1/2/3", "Ghi chú", "Khiển trách", ...
    - nhiều mục đánh số 1..999 dạng "20. ..."

    NEW (v2): yêu cầu "Lần 1", "Lần 2", "Lần 3" phải xuất hiện CÙNG TRÊN MỘT DÒNG
    (đặc trưng của dòng tiêu đề cột trong bảng phụ lục).  Văn bản quy chế nói về
    kỷ luật thường dùng "lần 1", "lần 2" trong câu riêng biệt → không thỏa điều kiện này.
    """
    blob = "\n".join(lines or [])
    f = vnfold(blob)

    # Kiểm tra số mục đánh số trước (nhanh, bỏ qua ngay nếu ít)
    item_count = sum(1 for l in (lines or []) if re2.search(r"^\s*\d{1,3}\.\s+\S", (l or "").strip()))
    if item_count < 10:
        return False

    # Kiểm tra nhanh toàn văn bản
    kws = [
        "lan 1", "lan 2", "lan 3", "lan 4",
        "ghi chu", "khien trach", "canh cao", "dinh chi", "buoc thoi hoc"
    ]
    global_hits = sum(1 for kw in kws if kw in f)
    if global_hits < 3:
        return False

    # NEW: kiểm tra "tiêu đề cột bảng" – "Lần 1", "Lần 2", "Lần 3" phải cùng một dòng.
    # Trong bảng phụ lục kỷ luật, 3 giá trị này là tên cột → xuất hiện cùng dòng.
    # Trong quy chế dạng văn xuôi, chúng xuất hiện ở 3 câu khác nhau.
    has_header_row = any(
        ("lan 1" in vnfold(l) and "lan 2" in vnfold(l) and "lan 3" in vnfold(l))
        for l in (lines or [])
    )
    return has_header_row

def remove_repeated_table_headers(text: str, min_repeats: int = 2) -> str:
    """
    Xoá các dòng header bảng lặp (ví dụ: 'Ghi chú Khiển trách ... Buộc thôi học')
    """
    raw_lines = [l.rstrip("\n") for l in (text or "").splitlines()]
    norm = [l.strip() for l in raw_lines if l.strip()]

    from collections import Counter
    cnt = Counter(norm)

    def is_table_header_line(l: str) -> bool:
        f = vnfold(l)
        # 2 kiểu header thường gặp
        if ("ghi chu" in f and "khien" in f and "buoc" in f):
            return True
        if ("lan 1" in f and "lan 2" in f):
            return True
        return False

    out = []
    for l in raw_lines:
        ls = l.strip()
        if ls and is_table_header_line(ls) and cnt.get(ls, 0) >= min_repeats:
            continue
        out.append(l)

    return "\n".join(out).strip()

def tokenize_tokens(text: str) -> List[int]:
    if _enc:
        return _enc.encode(text)
    # naive fallback
    return [1 for _ in re.findall(r"\w+", text, flags=re.UNICODE)]

def token_count(text: str) -> int:
    return len(tokenize_tokens(text))

# -------------------------
# Vietnamese structure detection
# -------------------------
PART_RE = re2.compile(r"^\s*phan\s+([ivxlcdm]+|\d+)\b", re2.I)
CHAPTER_RE = re2.compile(r"^\s*chuong\s+([ivxlcdm]+|\d+)\b", re2.I)
ARTICLE_RE = re2.compile(r"^\s*dieu\s+(\d+[a-z]?)\b", re2.I)
CLAUSE_RE = re2.compile(r"^\s*khoan\s+(\d+)\b", re2.I)

# Nếu văn bản có "Mục 1", "Tiểu mục 1.1" (tuỳ tài liệu)
SECTION_RE = re2.compile(r"^\s*muc\s+(\d+[a-z]?)\b", re2.I)
SUBSECTION_RE = re2.compile(r"^\s*tieu\s*muc\s+(\d+(?:\.\d+)*)\b", re2.I)

ITEM_RE = re2.compile(r"^\s*(\d{1,3})\s*[\.\)]\s+\S")
# IMPORTANT: điểm phải match trên raw lower (giữ 'đ')
LETTER_POINT_RE_RAW = re2.compile(r'^\s*[“"\(\[]?\s*([a-zđ])\s*\)\s+\S', re2.I)
DASH_RE = re2.compile(r"^\s*[-•]\s+\S")

# Explicit "Điểm X" marker – matched on lower_keep_vietnamese() output so 'đ' is preserved.
# Handles: "Điểm a", "điểm đ", "Điểm b.", "điêm c)" etc.
# Must be checked AFTER LETTER_POINT_RE_RAW (which catches the shorter "a) text" form).
DIEM_EXPLICIT_RAW = re2.compile(r"^\s*đi[eê]m\s+([a-zđ])\b", re2.I)

DECIMAL_ITEM_RE = re2.compile(r"^\s*([1-9]\d?)(?:\.\d{1,2}){1,3}\b")

def _extract_int(s: Optional[str]) -> Optional[int]:
    if not s:
        return None
    m = re.search(r"(\d+)", s)
    return int(m.group(1)) if m else None

def _extract_point(s: Optional[str]) -> Optional[str]:
    """Trích ký tự điểm từ chuỗi kiểu 'Điểm a', 'điểm đ', 'Diem b'.

    Lỗi cũ: dùng lower_keep_vietnamese() giữ nguyên 'điểm' (Unicode) nhưng
    regex lại tìm 'diem' (ASCII), nên không bao giờ khớp và luôn trả về None.

    Sửa: thử khớp Unicode trực tiếp trên chuỗi gốc trước, sau đó fallback sang
    vnfold() (ASCII) để bắt các biến thể như 'Diem a', 'DIEM B'.
    """
    if not s:
        return None
    # Primary: Unicode-aware – khớp "Điểm a", "điểm đ", "ĐIỂM B." v.v.
    # [Đđ][Ii][^\s]{1,2}[Mm] bắt toàn bộ biến thể NFC của "điểm"
    # (ể = U+1EC3, ề = U+1EC1, v.v.) mà không cần liệt kê từng ký tự.
    m = re.search(r"[Đđ][Ii][^\s]{1,2}[Mm]\s+([a-zđ])\b", s, re.I)
    if m:
        return m.group(1).lower()
    # Fallback: ASCII-folded – bắt "Diem a", "diem d" (đ→d khi fold là chấp nhận được)
    m = re2.search(r"(?i)\bdiem\s+([a-z])\b", vnfold(s))
    return m.group(1) if m else None

def is_decimal_heavy(lines: List[str], min_hits: int = 12, ratio: float = 0.08) -> bool:
    """
    Bật chế độ 'appendix/table' nếu có nhiều dòng bắt đầu bằng 1.1.1 / 2.2.7 / 6.4.5...
    """
    non_empty = [l for l in lines if (l or "").strip()]
    if not non_empty:
        return False

    hits = 0
    for l in non_empty:
        if DECIMAL_ITEM_RE.match((l or "").strip()):
            hits += 1

    return (hits >= min_hits) and (hits / len(non_empty) >= ratio)

def detect_structure(lines: List[str], table_mode: bool = False) -> List[Dict[str, object]]:
    segs: List[Dict[str, object]] = []

    current = {
        "part": None,
        "chapter": None,
        "section": None,
        "subsection": None,
        "article": None,
        "clause": None,
        "point": None,
        "page": None,
        "role": None,
    }

    decimal_mode = is_decimal_heavy(lines)

    PAGE_MARK_RE = re2.compile(r"^<<<PAGE:(\d+)>>>$")

    for idx, line in enumerate(lines):
        raw = (line or "").strip()
        if not raw:
            continue

        # page marker (optional)
        pm = PAGE_MARK_RE.match(raw)
        if pm:
            current["page"] = int(pm.group(1))
            continue

        f = fold_for_match(raw)
        raw_lower = lower_keep_vietnamese(raw)
        matched = False

        # --- Part ---
        m = PART_RE.match(f)
        if m:
            part = m.group(1)
            current["part"] = f"Phần {str(part).upper()}"
            # reset lower levels
            current["chapter"] = current["section"] = current["subsection"] = None
            current["article"] = current["clause"] = current["point"] = None
            current["role"] = "part_title"
            matched = True

        # --- Chapter ---
        if not matched:
            m = CHAPTER_RE.match(f)
            if m:
                ch = m.group(1)
                current["chapter"] = f"Chương {str(ch).upper()}"
                current["section"] = current["subsection"] = None
                current["article"] = current["clause"] = current["point"] = None
                current["role"] = "chapter_title"
                matched = True

        # --- Section / Subsection (optional) ---
        if not matched:
            m = SECTION_RE.match(f)
            if m:
                sec = m.group(1)
                current["section"] = f"Mục {sec}"
                current["subsection"] = None
                current["article"] = current["clause"] = current["point"] = None
                current["role"] = "section_title"
                matched = True

        if not matched:
            m = SUBSECTION_RE.match(f)
            if m:
                sub = m.group(1)
                current["subsection"] = f"Tiểu mục {sub}"
                current["article"] = current["clause"] = current["point"] = None
                current["role"] = "subsection_title"
                matched = True

        # --- Article ---
        if not matched:
            m = ARTICLE_RE.match(f)
            if m:
                a = m.group(1)
                current["article"] = f"Điều {a}"
                current["clause"] = None
                current["point"] = None
                current["role"] = "article"
                matched = True

        # --- Clause ---
        if not matched:
            m = CLAUSE_RE.match(f)
            if m:
                k = m.group(1)
                current["clause"] = f"Khoản {k}"
                current["point"] = None
                current["role"] = "clause"
                matched = True

        # --- Decimal / item / point ---
        if not matched:
            if decimal_mode:
                m = DECIMAL_ITEM_RE.match(raw)
                if m:
                    code = m.group(0).strip()
                    current["clause"] = f"Mục {code}"
                    current["point"] = None
                    current["role"] = "decimal_item"
                    matched = True

            if not matched:
                m = ITEM_RE.match(raw)
                if m:
                    k = m.group(1)
                    current["clause"] = (f"TT {k}" if table_mode else f"Khoản {k}")
                    current["point"] = None
                    current["role"] = "clause" if not table_mode else "table_item"
                    matched = True

            if not matched:
                m = LETTER_POINT_RE_RAW.match(raw_lower)
                if m:
                    p = m.group(1)  # 'd' hoặc 'đ' đúng
                    current["point"] = f"Điểm {p}"
                    current["role"] = "point"
                    matched = True

            # Explicit "Điểm X" format (e.g. "Điểm a.", "điểm đ -")
            # Matched on lower_keep_vietnamese so 'đ' is preserved correctly.
            if not matched:
                m = DIEM_EXPLICIT_RAW.match(raw_lower)
                if m:
                    p = m.group(1)
                    current["point"] = f"Điểm {p}"
                    current["role"] = "point"
                    matched = True

        if matched:
            path = [
                x
                for x in [
                    current.get("part"),
                    current.get("chapter"),
                    current.get("section"),
                    current.get("subsection"),
                    current.get("article"),
                    current.get("clause"),
                    current.get("point"),
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
                    "point": _extract_point(current.get("point")),  # supports 'đ'
                    "page": current.get("page"),
                    "role": current.get("role"),
                }
            )

    for i in range(len(segs)):
        segs[i]["end_idx"] = segs[i + 1]["start_idx"] if i + 1 < len(segs) else len(lines)

    return segs

def build_doc_id(
    article_no: Optional[int],
    clause_no: Optional[int],
    point: Optional[str],
    i: int,
    prefix: str = "A",
) -> str:
    a = article_no if article_no is not None else "NA"
    c = clause_no if clause_no is not None else "NA"
    if point is None:
        p = "NA"
    else:
        # để doc_id không bị lỗi ký tự 'đ' trong một số hệ thống
        p = "dd" if point == "đ" else point
    return f"{prefix}-{a}-{c}-{p}-{i}"

# -------------------------
# Soft-wrap normalization (PDF line wrapping)
# -------------------------
def normalize_soft_wrap(text: str) -> str:
    """
    Merge lines broken by PDF wrap, while preserving real paragraphs.
    Improvements:
      - Join hyphenated breaks: "quy-" + "định" => "quyđịnh" (remove '-')
      - Join single-letter splits: "đ" + "iểm" => "điểm" (NO SPACE)
    """
    raw_lines = [l.rstrip() for l in (text or "").splitlines()]
    lines = [l for l in raw_lines if l is not None]

    def is_marker(line: str) -> bool:
        s = (line or "").strip()
        if not s:
            return True
        # Page-boundary markers must be treated as paragraph breaks so they are
        # never merged with surrounding text (they will be stripped from output).
        if re2.match(r"^<<<PAGE:\d+>>>$", s):
            return True
        f = fold_for_match(s)
        raw_lower = lower_keep_vietnamese(s)

        if CHAPTER_RE.match(f) or ARTICLE_RE.match(f) or CLAUSE_RE.match(f):
            return True
        if ITEM_RE.match(s):
            return True
        if LETTER_POINT_RE_RAW.match(f):
            return True
        if DASH_RE.match(s):
            return True
        if PART_RE.match(f) or SECTION_RE.match(f) or SUBSECTION_RE.match(f):
            return True
        return False

    out: List[str] = []
    buf = ""

    for line in lines:
        s = (line or "").strip()

        # paragraph break
        if not s:
            if buf:
                out.append(buf.strip())
                buf = ""
            out.append("")
            continue

        if not buf:
            buf = s
            continue

        prev = buf.rstrip()
        prev_end_strong = bool(re.search(r"[\.!?…:;”\"]\s*$", prev))

        # If next line is a marker, flush current buffer first.
        if is_marker(s):
            out.append(buf.strip())
            buf = s
            continue

        joinable = (not prev_end_strong) and (not is_marker(prev))
        looks_continuation = bool(re.match(r"^[a-zà-ỹ0-9(“\"']", s, flags=re.I))

        # --- NEW: join-without-space heuristics ---
        last_word = prev.split()[-1] if prev.split() else ""
        last_fold = vnfold(last_word)

        # (1) Hyphenation: "quy-" + "định" -> "quyđịnh"
        if prev.endswith("-"):
            buf = prev[:-1] + s
            continue

        # (2) Single-letter split: "đ" + "iểm" -> "điểm" (no space)
        if len(last_fold) == 1 and last_fold.isalpha() and re.match(r"^[a-zà-ỹ]", s, flags=re.I):
            buf = prev + s
            continue

        # default join with space
        if joinable or looks_continuation:
            buf = prev + " " + s
        else:
            out.append(buf.strip())
            buf = s

    if buf:
        out.append(buf.strip())

    return "\n".join(out)

def fix_missing_space_after_roman(text: str) -> str:
    """
    Fix kiểu: 'Chương IPhạm...' -> 'Chương I Phạm...'
             'Phần INHỮNG...'  -> 'Phần I NHỮNG...'
    """
    s = text or ""
    s = re2.sub(r"(?i)\b(chuong|phan)\s+([ivxlcdm]+)(?=[a-zà-ỹ])", r"\1 \2 ", s)
    return s


# NEW: fix "Điều" split across lines (PDF extraction artifact)
# Regex khớp "Điều" hoặc "ĐIỀU" ở cuối dòng, theo sau là số điều ở đầu dòng tiếp.
# Ví dụ:   "...quy định\nĐiều\n12. Tiêu đề" → "...quy định\nĐiều 12. Tiêu đề"
_DIEU_SPLIT_RE = re.compile(
    r"([ĐđD][Ii][^\s]{0,3}[Uu])\s*\n\s*(\d{1,3}[a-zđ]?[.\s])",
    re.UNICODE,
)


def fix_dieu_number_split(text: str) -> str:
    """Nối 'Điều\\nN. Tiêu đề' → 'Điều N. Tiêu đề' (artifact PDF tách dòng).

    Khi PDF trích xuất văn bản, đôi khi 'Điều' và số thứ tự nằm trên hai
    dòng khác nhau khiến ARTICLE_RE không khớp được.  Hàm này dùng regex
    Unicode (không phải re2) để bắt mọi biến thể NFC của 'Điều'/'ĐIỀU'.
    """
    return _DIEU_SPLIT_RE.sub(r"\1 \2", text or "")


# -------------------------
# Paragraph break injection (marker-based)
# -------------------------
def inject_paragraph_breaks(text: str) -> str:
    """
    Create blank lines before structural markers so chunk_by_tokens()
    can chunk by paragraphs instead of cutting mid-structure.

    Markers:
      - Chương / Điều / Khoản
      - "1." / "2)"
      - "b)" / "c)" (even quoted)
      - Lines ending with ":" (e.g., "Nơi nhận:")
      - Bullet lines "- ..." / "• ..." as paragraph boundaries (not structure)
    """
    raw_lines = [l.rstrip() for l in (text or "").splitlines()]
    out: List[str] = []

    def is_marker_line(s: str) -> bool:
        f = fold_for_match(s.strip())
        if not f:
            return False
        # Page-boundary markers → inject blank line before each new page.
        if re2.match(r"^<<<PAGE:\d+>>>$", s.strip()):
            return True
        if CHAPTER_RE.match(f) or ARTICLE_RE.match(f) or CLAUSE_RE.match(f):
            return True
        if ITEM_RE.match(s):
            return True
        raw_lower = lower_keep_vietnamese(s)
        if LETTER_POINT_RE_RAW.match(f):
            return True
        if re.search(r":\s*$", s.strip()):
            return True
        if DASH_RE.match(s):
            return True
        if PART_RE.match(f) or SECTION_RE.match(f) or SUBSECTION_RE.match(f):
            return True
        return False

    for line in raw_lines:
        s = (line or "").strip()
        if not s:
            out.append("")
            continue

        if is_marker_line(s) and out and out[-1] != "":
            out.append("")

        out.append(s)

    return "\n".join(out)

# -------------------------
# Chunking (paragraph-first, token-decode fallback)
# -------------------------
def _trim_partial_word_edges(s: str, trim_left: bool, trim_right: bool) -> str:
    out = (s or "").strip()
    if not out:
        return out

    if trim_left:
        out2 = re.sub(r"^\S+\s+", "", out, count=1, flags=re.UNICODE)
        if out2.strip():
            out = out2.strip()

    if trim_right:
        out2 = re.sub(r"\s+\S+$", "", out, count=1, flags=re.UNICODE)
        if out2.strip():
            out = out2.strip()
    return out


def _chunk_token_window(text: str, target_tokens: int, overlap_ratio: float) -> List[str]:
    if not text.strip():
        return []
    if not _enc:
        return [text.strip()]

    toks = _enc.encode(text)
    if len(toks) <= target_tokens:
        return [text.strip()]

    step = max(1, int(target_tokens * (1 - overlap_ratio)))
    chunks: List[str] = []
    n = len(toks)

    for start in range(0, n, step):
        end = min(n, start + target_tokens)
        piece = _enc.decode(toks[start:end]).strip()

        piece = _trim_partial_word_edges(
            piece,
            trim_left=(start > 0),
            trim_right=(end < n),
        )

        if piece:
            chunks.append(piece)

        if end >= n:
            break

    out2: List[str] = []
    seen = set()
    for c in chunks:
        key = c[:120]
        if key not in seen:
            seen.add(key)
            out2.append(c)
    return out2


def chunk_by_tokens(text: str, target_tokens: int = 220, overlap_ratio: float = 0.25) -> List[str]:
    """
    Better chunking for RAG:
      - Prefer paragraph packing (keeps meaning)
      - If a paragraph is too long -> token-window chunk
      - Optional overlap (token-based) to reduce boundary loss
    """
    t = (text or "").strip()
    if not t:
        return []
    if target_tokens <= 0:
        return [t]

    paras = [p.strip() for p in re.split(r"\n\s*\n", t) if p.strip()]
    if not paras:
        return [t]

    chunks: List[str] = []
    cur: List[str] = []
    cur_tok = 0

    def tokcnt(x: str) -> int:
        return token_count(x)

    for para in paras:
        p_tok = tokcnt(para)

        if p_tok > int(target_tokens * 1.2):
            if cur:
                chunks.append("\n\n".join(cur).strip())
                cur, cur_tok = [], 0
            chunks.extend(_chunk_token_window(para, target_tokens, overlap_ratio))
            continue

        if cur and (cur_tok + p_tok) > target_tokens:
            chunks.append("\n\n".join(cur).strip())
            cur, cur_tok = [], 0

        cur.append(para)
        cur_tok += p_tok

    if cur:
        chunks.append("\n\n".join(cur).strip())

    if _enc and overlap_ratio > 0 and len(chunks) > 1:
        tail_tok = int(target_tokens * overlap_ratio)
        if tail_tok > 0:
            out3 = [chunks[0]]
            prev_tokens = _enc.encode(chunks[0])

            for i in range(1, len(chunks)):
                tail = prev_tokens[-tail_tok:] if len(prev_tokens) > tail_tok else prev_tokens
                prefix = _enc.decode(tail).strip()

                # Dedup check: tiktoken often cuts a Vietnamese word at the start of `prefix`
                # (e.g., prefix = "dạy Ong…" where "dạy" is the tail of prev chunk).
                # Try stripping 1–3 leading orphan words until we find the match in chunks[i].
                chunk_s = chunks[i].strip()
                dedup_found = chunk_s.startswith(prefix)
                if not dedup_found:
                    candidate = prefix
                    for _ in range(3):
                        candidate = _trim_partial_word_edges(
                            candidate, trim_left=True, trim_right=False
                        )
                        if len(candidate) < 20:   # too short to be a reliable match signal
                            break
                        if chunk_s.startswith(candidate):
                            dedup_found = True
                            break

                merged = chunk_s if dedup_found else (prefix + "\n\n" + chunk_s).strip()

                out3.append(merged)
                prev_tokens = _enc.encode(chunks[i])

            chunks = out3

    return [c for c in chunks if c]


# -------------------------
# Cleaning headers/footers
# -------------------------
def remove_headers_footers(pages: List[str]) -> List[str]:
    from collections import Counter

    first_lines, last_lines = [], []
    for p in pages:
        ls = [l.strip() for l in (p or "").splitlines() if l.strip()]
        if not ls:
            continue
        first_lines.extend(ls[:3])
        last_lines.extend(ls[-3:])

    counts = Counter(first_lines + last_lines)
    threshold = max(2, int(0.5 * max(1, len(pages))))
    repetitive = {s for s, c in counts.items() if c >= threshold}
    # Soft threshold: ngưỡng thấp hơn (30%) cho các dòng ngắn < 60 ký tự.
    # Dòng ngắn lặp lại (tên trường, số văn bản, …) thường là header/footer.
    threshold_soft = max(2, int(0.3 * max(1, len(pages))))
    repetitive |= {
        s for s, c in counts.items()
        if c >= threshold_soft and len(s) < 60
    }

    # Boilerplate patterns phổ biến trong văn bản hành chính Việt Nam.
    # Compiled once, applied to vnfold()-ed lines.
    _BOILERPLATE_RE = re2.compile(
        r"(cong hoa xa hoi chu nghia viet nam"
        r"|doc lap.{0,10}tu do.{0,10}hanh phuc"
        r"|truong dai hoc|dai hoc quoc gia"
        r"|bo giao duc va dao tao"
        r"|so:\s*\S+[-/]\S+)",
        re2.I,
    )

    cleaned_pages = []
    for p in pages:
        new_lines = []
        for l in (p or "").splitlines():
            lt = l.strip()
            lf = vnfold(lt)
            if not lt:
                continue
            if re2.search(r"^(page\s*)?\d+\s*/?\s*\d*$", lf):
                continue
            if lt in repetitive:
                continue
            if re2.search(r"(watermark|confidential|draft)", lf):
                continue
            if _BOILERPLATE_RE.search(lf):
                continue
            new_lines.append(lt)
        cleaned_pages.append("\n".join(new_lines))
    return cleaned_pages

def remove_page_overlaps(pages: List[str], max_tail_lines: int = 8, max_head_lines: int = 8) -> List[str]:
    """
    Xoá phần lặp giữa đuôi trang trước và đầu trang sau.
    Rất hay gặp khi extract text PDF.
    """
    out = []
    prev_lines: List[str] = []

    for p in pages or []:
        lines = [l.strip() for l in (p or "").splitlines() if l.strip()]
        if not lines:
            out.append("")
            prev_lines = []
            continue

        cut = 0
        max_k = min(max_tail_lines, len(prev_lines), max_head_lines, len(lines))
        for k in range(max_k, 0, -1):
            if prev_lines[-k:] == lines[:k]:
                cut = k
                break

        if cut > 0:
            lines = lines[cut:]

        out.append("\n".join(lines))
        prev_lines = lines

    return out

def dedupe_consecutive_lines(text: str) -> str:
    """
    Xoá các dòng trùng liên tiếp (hay gặp khi extract PDF bảng bị lặp block).
    """
    out = []
    prev = None
    for line in (text or "").splitlines():
        l = line.strip()
        if not l:
            out.append("")
            prev = None
            continue
        if l == prev:
            continue
        out.append(line)
        prev = l
    return "\n".join(out).strip()

# -------------------------
# OCR postprocess + scoring
# -------------------------
def normalize_ocr_text(text: str) -> str:
    """
    Làm sạch nhẹ text sau OCR:
      - bỏ ký tự rác hay gặp
      - sửa nhầm số thứ tự kiểu L2. -> 12. ; I3. -> 13.
      - đảm bảo mỗi mục 1..99 bắt đầu ở đầu dòng (giúp parse theo ITEM_RE)
      - chuẩn hoá khoảng trắng
    """
    s = (text or "")

    # 1) Loại bỏ một số ký tự rác phổ biến
    garbage_chars = ["Ø", "¡", "¢", "§", "�", "^", "|"]
    for ch in garbage_chars:
        s = s.replace(ch, " ")

    # 2) Sửa nhầm đầu dòng: "L2." / "I3." => "12." / "13."
    # (L/I thường bị OCR nhầm với số 1)
    s = re2.sub(r"(?m)^\s*[LI]\s*(\d)\s*\.", r"1\1.", s)

    # 3) Sửa nhầm dấu chấm thành dấu phẩy ở đầu dòng: "7," => "7."
    s = re2.sub(r"(?m)^\s*(\d{1,2})\s*,\s+", r"\1. ", s)

    # 4) Nếu số thứ tự bị dính vào câu trước, ép xuống dòng trước "12. ...", "3. ..."
    # Ví dụ: "... xe buýt. 12. Cấm ..." -> "\n12. Cấm ..."
    s = re2.sub(r"(?<!\n)\s+(\d{1,2})\.\s+", r"\n\1. ", s)

    # 5) Chuẩn hoá khoảng trắng
    s = re2.sub(r"[ \t]+", " ", s)
    s = re2.sub(r"\n{3,}", "\n\n", s)

    # 6) Xóa dòng chỉ gồm dấu phân cách: gạch ngang ASCII/en-dash/em-dash, dấu bằng, v.v.
    #    Ví dụ: "——", "— ====", "_______________", "a) ................................"
    #    (Mở rộng từ bản cũ: thêm –— vào charset, giảm ngưỡng từ 5 xuống 2)
    s = re2.sub(r"(?m)^\s*[-–—=~_\.…]{2,}\s*$", "", s)

    # 6b) Xóa dòng rác rất ngắn (≤ 5 ký tự) không có ký tự tiếng Việt và không phải
    #     số thứ tự (1. / 12) / ...). Xuất hiện do OCR đọc nhầm viền/khung trang.
    #     Ví dụ: "eee:", "HH.", "es 2", "srr |", "th", "|"
    _noise_filtered: List[str] = []
    for _ln in s.splitlines():
        _ls = _ln.strip()
        if _ls and len(_ls) <= 5:
            _has_vi = any(
                0x00C0 <= ord(c) <= 0x024F or 0x1E00 <= ord(c) <= 0x1EFF
                for c in _ls
            )
            _is_num = bool(re2.match(r"\d{1,3}[\.\)]", _ls))
            if not _has_vi and not _is_num:
                _noise_filtered.append("")  # thay bằng dòng trống (giữ cấu trúc)
                continue
        _noise_filtered.append(_ln)
    s = "\n".join(_noise_filtered)
    s = re2.sub(r"\n{3,}", "\n\n", s)  # gộp lại các dòng trống liên tiếp

    # 7) Xóa ký tự thay thế U+FFFD (từ watermark/stamp bị OCR đọc sai font)
    s = s.replace("\ufffd", "")

    # 8) Xóa tiền tố ':' đơn độc đầu dòng – OCR hay sinh ra từ góc trang hoặc con dấu
    #    Ví dụ: ":  Điều 3." xuất hiện ở đầu trang do dấu đóng khung bị nhận nhầm là ':'
    s = re2.sub(r"(?m)^\s*:\s+", "", s)

    return s.strip()


def ocr_text_quality_score(text: str) -> float:
    """
    Chấm điểm output OCR để chọn bản tốt nhất (multi-pass):
      - ưu tiên nhiều chữ cái (alpha)
      - phạt ký tự rác
      - phạt nếu số chiếm quá nhiều
      - thưởng nhẹ nếu text dài hơn (tối đa 20k ký tự)
    """
    t = (text or "").strip()
    if not t:
        return -1e9

    alpha = sum(ch.isalpha() for ch in t)
    digits = sum(ch.isdigit() for ch in t)
    total = max(1, len(t))

    # đếm một số ký tự rác
    garbage = sum(t.count(c) for c in ["€", "¢", "§", "�", "Ø", "¡", "^"])

    alpha_ratio = alpha / total
    digit_ratio = digits / total
    length_bonus = min(len(t), 20000) / 20000.0

    return alpha_ratio * 2.0 - digit_ratio * 0.5 - garbage * 0.05 + length_bonus


def preprocess_ocr_image(img):
    """
    Tiền xử lý ảnh trước OCR (PIL-only):
      - grayscale
      - autocontrast
      - sharpen nhẹ
      - threshold vừa phải (đỡ mất nét số nhỏ như 11/12/13)
    """
    if Image is None:
        return img

    from PIL import ImageOps, ImageFilter

    img = img.convert("L")
    img = ImageOps.autocontrast(img)
    img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))

    # threshold: bạn có thể tune 190-210 tuỳ scan
    thresh = 200
    img = img.point(lambda x: 0 if x < thresh else 255)
    return img

# -------------------------
# OCR paragraph repair
# -------------------------
def merge_ocr_paragraph_artifacts(text: str) -> str:
    """Nối các "paragraph" giả do OCR tạo ra khi Tesseract chia một câu thành
    nhiều text-block riêng biệt, chèn dòng trống giữa chừng câu.

    Nguyên tắc: nếu đoạn trước KHÔNG kết thúc bằng dấu câu (.!?:;) VÀ đoạn
    tiếp theo bắt đầu bằng chữ thường (continuation), nối lại bằng khoảng trắng.

    Không nối nếu đoạn sau:
      - là marker cấu trúc (Điều, Khoản, Chương, Phần, mục số "1. ", ...)
      - là page marker <<<PAGE:N>>>
    """
    _SENT_END = re.compile(r'[.!?:;"""»\]\)]\s*$')
    _STRUCT_START = re2.compile(
        r"^\s*(dieu\s+\d|khoan\s+\d|chuong\s+[ivx\d]|phan\s+[ivx\d]"
        r"|\d{1,3}[\.\)]\s|[a-z\u0111]\)\s|<<<page:\d+>>>)",
        re2.I,
    )

    paras = re.split(r"\n\n+", (text or "").strip())
    out: List[str] = []
    buf = ""

    for para in paras:
        para = para.strip()
        if not para:
            if buf:
                out.append(buf)
                buf = ""
            continue

        if buf:
            is_struct = bool(_STRUCT_START.match(vnfold(para[:40])))
            no_punct = not _SENT_END.search(buf)
            # continuation: starts with lowercase letter (incl. Vietnamese), not a structure marker
            looks_cont = bool(re.match(r"^[a-zà-ỹ]", para, re.IGNORECASE)) and not is_struct

            if no_punct and looks_cont:
                buf = buf + " " + para
                continue

        if buf:
            out.append(buf)
        buf = para

    if buf:
        out.append(buf)

    return "\n\n".join(out)


# -------------------------
# PDF extraction + OCR
# -------------------------
def _extract_text_pdf_default(path: str) -> Tuple[str, List[str]]:
    if fitz is not None:
        doc = fitz.open(path)
        pages = []
        for page in doc:
            pages.append(page.get_text("text") or "")
        full_text = "\n\n".join(pages).strip()
        return full_text, pages

    try:
        import pdfplumber
    except ModuleNotFoundError:
        return "", []

    pages = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text() or "")
    full_text = "\n\n".join(pages).strip()
    return full_text, pages


def ocr_pdf(path: str, dpi: int = 450, lang: str = "vie+eng", psm: int = 6) -> Tuple[str, List[str]]:
    """OCR PDF pages using PyMuPDF rasterization + pytesseract. Return (full_text, pages_text_list)."""
    if fitz is None:
        raise RuntimeError("PyMuPDF chưa được cài. Cài: pip install pymupdf")

    if Image is None or pytesseract is None or io is None:
        raise RuntimeError("Thiếu pillow/pytesseract. Cài: pip install pillow pytesseract")

    # Ensure pytesseract knows tesseract.exe (Windows)
    tcmd = os.environ.get("TESSERACT_CMD") or DEFAULT_TESSERACT_CMD
    if tcmd and os.path.exists(tcmd):
        pytesseract.pytesseract.tesseract_cmd = tcmd

    doc = fitz.open(path)
    pages_text: List[str] = []

    zoom = dpi / 72
    mat = fitz.Matrix(zoom, zoom)

    # Multi-pass OCR: thử vài chế độ psm và chọn output tốt nhất
    psm_candidates = [6, 4, 11]  # 6: text block/list; 4: multi-column; 11: sparse
    base_cfg = "--oem 1 -c preserve_interword_spaces=1"

    for page in doc:
        # Render grayscale để OCR ổn hơn
        pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY, alpha=False)
        img = Image.open(io.BytesIO(pix.tobytes("png")))

        # tiền xử lý ảnh
        img = preprocess_ocr_image(img)

        best_txt = ""
        best_score = -1e18

        for psm_ in psm_candidates:
            cfg = f"{base_cfg} --psm {psm_}"
            txt = pytesseract.image_to_string(img, lang=lang, config=cfg) or ""
            score = ocr_text_quality_score(txt)
            if score > best_score:
                best_score = score
                best_txt = txt

        pages_text.append(best_txt.strip())

    full_text = "\n\n".join(pages_text).strip()
    return full_text, pages_text


def text_from_pdf(path: str) -> Tuple[str, List[str]]:
    full_text, pages = _extract_text_pdf_default(path)
    full_text = (full_text or "").strip()
    pages = [p or "" for p in (pages or [])]

    MIN_CHARS = 300
    _used_ocr = False
    if len(full_text) < MIN_CHARS or looks_garbled(full_text):
        reason = (
            f"text too short ({len(full_text)} chars)"
            if len(full_text) < MIN_CHARS
            else "garbled/no Vietnamese diacritics"
        )
        print(f"    [OCR] triggering OCR on '{path}' — reason: {reason}")
        try:
            full_text, pages = ocr_pdf(path, dpi=450, lang="vie+eng", psm=6)
            # Post-process OCR output: fix item numbering + garbage chars
            full_text = normalize_ocr_text(full_text)
            pages = [normalize_ocr_text(p) for p in (pages or [])]
            # Nối các "paragraph" giả OCR (câu bị tách ở giữa do text-block boundary)
            pages = [merge_ocr_paragraph_artifacts(p) for p in pages]
            full_text = "\n\n".join(pages).strip()
            _used_ocr = True
            _chars = len(full_text)
            print(f"    [OCR] done — extracted {_chars} chars  ({'ok' if _chars > MIN_CHARS else 'WARNING: still short'})")
        except Exception as e:
            print(f"    [OCR] FAILED: {e}")
            return full_text, pages

    # Remove repeated header/footer lines (runs on both text + OCR output)
    pages_clean = remove_headers_footers(pages)
    if sum(len(p) for p in pages_clean) >= 0.3 * max(1, sum(len(p) for p in pages)):
        pages = pages_clean
        full_text = "\n\n".join(pages).strip()

    return full_text, pages

def looks_garbled(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return True

    garbage_chars = sum(t.count(c) for c in ["€", "¢", "§", "�"])
    alpha = sum(ch.isalpha() for ch in t)
    digits = sum(ch.isdigit() for ch in t)
    total = max(1, len(t))

    alpha_ratio = alpha / total
    digit_ratio = digits / total

    # Nếu chữ cái quá ít hoặc số chen quá nhiều hoặc nhiều ký tự rác => bẩn
    if (alpha_ratio < 0.45) or (digit_ratio > 0.20) or (garbage_chars >= 3):
        return True

    # NEW: kiểm tra mật độ ký tự có dấu tiếng Việt.
    # Văn bản tiếng Việt hợp lệ dài (> 300 ký tự) phải có ≥ 0.5 % ký tự có dấu.
    # Tỷ lệ gần 0 nghĩa là font bị mã hóa sai: mỗi chữ Việt được ánh xạ sang
    # ký tự ASCII không dấu (e.g. "DAr HQC" thay vì "ĐẠI HỌC").
    # Trường hợp này OCR sẽ cho kết quả tốt hơn nhiều.
    if total > 300 and _vietnamese_char_ratio(t) < 0.005:
        return True

    return False


# -------------------------
# DOCX extraction
# -------------------------
def text_from_docx(path: str) -> str:
    from docx import Document

    doc = Document(path)
    return "\n".join(p.text for p in doc.paragraphs)


def list_files_recursively(in_paths: List[str]) -> List[Path]:
    """Recursively collect PDF/DOCX files, deduplicating by resolved path.

    Windows has a case-insensitive filesystem, so rglob("*.pdf") and
    rglob("*.PDF") return the same files.  We resolve() each path and use
    a seen-set to avoid processing any file twice.
    """
    out: List[Path] = []
    seen: set = set()
    for p in in_paths:
        pth = Path(p)
        if pth.is_dir():
            for ext in ("*.pdf", "*.docx"):   # lowercase only; Windows matching is case-insensitive
                for f in sorted(pth.rglob(ext)):
                    key = f.resolve()
                    if key not in seen:
                        seen.add(key)
                        out.append(f)
        elif pth.exists():
            key = pth.resolve()
            if key not in seen:
                seen.add(key)
                out.append(pth)
    return out
