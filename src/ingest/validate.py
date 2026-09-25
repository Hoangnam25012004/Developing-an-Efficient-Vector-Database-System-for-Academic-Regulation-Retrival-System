"""Checks run on an uploaded file before it is allowed into the corpus.

Every rejection carries a stable code and a Vietnamese message, so the UI can
show the reason next to the file instead of a generic failure after the fact.
Nothing here trusts the file extension: the type is read from the bytes.
"""

from __future__ import annotations

import re
import shutil
import zipfile
from dataclasses import dataclass, field
from html import unescape
from pathlib import Path

# Messages shown to the user, keyed by reason code (design §6.3).
MESSAGES = {
    "UNSUPPORTED_TYPE": "Chỉ hỗ trợ PDF và DOCX.",
    "LEGACY_DOC": "File .doc cũ chưa được hỗ trợ — hãy mở bằng Word và lưu thành .docx hoặc .pdf.",
    "TYPE_MISMATCH": "Đuôi file không khớp nội dung ({detail}).",
    "EMPTY_FILE": "File rỗng.",
    "TOO_LARGE": "File vượt giới hạn {max_file_mb} MB.",
    "TOO_MANY_FILES": "Mỗi lần tối đa {max_files} file.",
    "BATCH_TOO_LARGE": "Tổng dung lượng một lần vượt {max_batch_mb} MB.",
    "TOO_MANY_PAGES": "Tài liệu vượt {max_pages} trang.",
    "ENCRYPTED": "File có mật khẩu — hãy gỡ mật khẩu rồi tải lại.",
    "CORRUPT": "Không mở được file (hỏng hoặc sai chuẩn).",
    "MACRO_DOCUMENT": "File chứa macro không được chấp nhận.",
    "ARCHIVE_TOO_LARGE": "Nội dung giải nén của DOCX quá lớn.",
    "NO_TEXT": "DOCX không có chữ (có thể chỉ chứa ảnh) — hãy xuất sang PDF để hệ thống OCR.",
    "NAME_INVALID": "Tên file không hợp lệ.",
    "DUPLICATE_CONTENT": "Nội dung trùng với «{source}» (nhóm {group}).",
    "NAME_CONFLICT_SAME_GROUP": "Đã có tài liệu cùng tên trong nhóm: chọn Thay thế, Đổi tên hoặc Bỏ qua.",
    "NAME_CONFLICT_OTHER_GROUP": "Tên đã dùng ở nhóm {group}: chọn Đổi tên hoặc Bỏ qua.",
    "NAME_CONFLICT_IN_BATCH": "Trùng tên với một file khác trong lần tải này: chọn Đổi tên hoặc Bỏ qua.",
    "DISK_FULL": "Không đủ dung lượng đĩa.",
    # processing (job) errors
    "PARSE_FAILED": "Lỗi khi đọc nội dung: {detail}",
    "EMPTY_CONTENT": "Không trích được nội dung — có thể là bản scan chất lượng thấp.",
    "OCR_UNAVAILABLE": "Tài liệu cần OCR nhưng Tesseract không khả dụng.",
    "QDRANT_UNAVAILABLE": "Không kết nối được Qdrant — hãy bật Qdrant rồi bấm Thử lại.",
    "EMBED_FAILED": "Lỗi khi tạo vector: {detail}",
    "INDEX_FAILED": "Lỗi khi cập nhật chỉ mục: {detail}",
    "FILE_MISSING": "Không tìm thấy file trong kho (có thể đã bị xoá).",
    "TIMEOUT": "Xử lý quá thời gian cho phép.",
    "INTERRUPTED": "Bị ngắt do server khởi động lại (tự thử lại).",
    "WORKER_CRASHED": "Tiến trình xử lý dừng bất thường: {detail}",
}


def message(code: str, **kw) -> str:
    template = MESSAGES.get(code, code)
    try:
        return template.format(**kw)
    except (KeyError, IndexError):
        return template


class Rejected(Exception):
    def __init__(self, code: str, **kw):
        super().__init__(code)
        self.code = code
        self.message = message(code, **kw)


# ─── Limits ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Limits:
    allowed_types: tuple[str, ...] = ("pdf", "docx")
    max_file_mb: float = 50
    max_files_per_upload: int = 20
    max_batch_mb: float = 200
    max_pages: int = 1000
    max_docx_uncompressed_mb: float = 300
    min_free_disk_mb: float = 1024

    @classmethod
    def from_cfg(cls, cfg: dict) -> "Limits":
        ing = (cfg or {}).get("ingest") or {}
        d = cls()
        return cls(
            allowed_types=tuple(str(t).lower().lstrip(".") for t in ing.get("allowed_types", d.allowed_types)),
            max_file_mb=float(ing.get("max_file_mb", d.max_file_mb)),
            max_files_per_upload=int(ing.get("max_files_per_upload", d.max_files_per_upload)),
            max_batch_mb=float(ing.get("max_batch_mb", d.max_batch_mb)),
            max_pages=int(ing.get("max_pages", d.max_pages)),
            max_docx_uncompressed_mb=float(ing.get("max_docx_uncompressed_mb", d.max_docx_uncompressed_mb)),
            min_free_disk_mb=float(ing.get("min_free_disk_mb", d.min_free_disk_mb)),
        )

    @property
    def max_file_bytes(self) -> int:
        return int(self.max_file_mb * 1024 * 1024)

    @property
    def max_batch_bytes(self) -> int:
        return int(self.max_batch_mb * 1024 * 1024)


def free_disk_ok(directory: Path, needed_bytes: int, limits: Limits) -> bool:
    try:
        free = shutil.disk_usage(directory).free
    except OSError:
        return True
    return free - needed_bytes >= limits.min_free_disk_mb * 1024 * 1024


# ─── Type sniffing ──────────────────────────────────────────────────────────

_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def sniff(head: bytes) -> str:
    if b"%PDF-" in head[:1024]:
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        return "zip"
    if head.startswith(_OLE_MAGIC):
        return "ole"
    return "unknown"


def _ole_is_encrypted_ooxml(path: Path) -> bool:
    """An encrypted .docx is an OLE container holding an EncryptionInfo stream;
    a legacy .doc is an OLE container without one."""
    try:
        with open(path, "rb") as f:
            data = f.read(4 * 1024 * 1024)
    except OSError:
        return False
    return "EncryptionInfo".encode("utf-16-le") in data


# ─── Language suggestion ────────────────────────────────────────────────────

# Same alphabet as the parser's _VN_DIACRITICS (01_parse_chunk.py).
_VN_DIACRITICS = frozenset(
    "àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợ"
    "ùúủũụưừứửữựỳýỷỹỵđ"
)
_EN_STOPWORDS = frozenset(
    "the of and to in a is for that on with as by be are this or from at an it "
    "not which shall will must all any may have has was were their its".split()
)


def diacritic_ratio(text: str) -> float:
    letters = [c for c in text.lower() if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if c in _VN_DIACRITICS) / len(letters)


def english_stopword_ratio(text: str) -> float:
    words = re.findall(r"[a-z]+", text.lower())
    if not words:
        return 0.0
    return sum(1 for w in words if w in _EN_STOPWORDS) / len(words)


def suggest_language(text: str, detect_cfg: dict | None = None) -> tuple[str, str]:
    """Return (language, reason).

    Measured on this corpus (design §7.4): the three scans whose text layer
    lost every diacritic score 0.000 / ≤0.009, healthy Vietnamese 0.293 /
    0.020, English 0.000 / 0.288 — so the stopword ratio is what separates
    English from a broken Vietnamese layer, which diacritics alone cannot.
    """
    cfg = detect_cfg or {}
    min_chars = int(cfg.get("min_chars", 200))
    vi_min = float(cfg.get("vi_min_diacritic_ratio", 0.05))
    en_min = float(cfg.get("en_min_stopword_ratio", 0.12))
    sample = (text or "")[:20000]
    if len(sample.strip()) < min_chars:
        return "vi", "little_text"
    if diacritic_ratio(sample) >= vi_min:
        return "vi", "diacritics"
    if english_stopword_ratio(sample) >= en_min:
        return "en", "english_stopwords"
    return "vi", "no_diacritics"


# ─── Per-type validation ────────────────────────────────────────────────────

@dataclass
class Inspection:
    file_type: str
    pages: int | None = None
    scanned_pages_estimate: int = 0
    text_sample: str = ""
    warnings: list[str] = field(default_factory=list)


OCR_SECONDS_PER_PAGE = 1.5


def _inspect_pdf(path: Path, limits: Limits) -> Inspection:
    import fitz

    try:
        doc = fitz.open(str(path))
    except Exception:
        raise Rejected("CORRUPT")
    try:
        if doc.needs_pass:
            raise Rejected("ENCRYPTED")
        n = doc.page_count
        if n < 1:
            raise Rejected("CORRUPT")
        if n > limits.max_pages:
            raise Rejected("TOO_MANY_PAGES", max_pages=limits.max_pages)
        k = min(10, n)
        idx = sorted({round(i * (n - 1) / max(k - 1, 1)) for i in range(k)})
        texts = []
        for i in idx:
            try:
                texts.append(doc[i].get_text("text"))
            except Exception:
                texts.append("")
        scanned = sum(1 for t in texts if len(t.strip()) < 50)
        estimate = round(scanned / len(idx) * n) if idx else 0
        return Inspection("pdf", pages=n, scanned_pages_estimate=estimate,
                          text_sample="\n".join(texts)[:20000])
    finally:
        doc.close()


_W_T = re.compile(rb"<w:t(?:\s[^>]*)?>([^<]*)</w:t>")


def _inspect_docx(path: Path, limits: Limits) -> Inspection:
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError):
        raise Rejected("CORRUPT")
    with z:
        infos = z.infolist()
        max_total = limits.max_docx_uncompressed_mb * 1024 * 1024
        if len(infos) > 10000 or sum(i.file_size for i in infos) > max_total:
            raise Rejected("ARCHIVE_TOO_LARGE")
        for i in infos:
            if i.file_size > 1024 * 1024 and i.compress_size and i.file_size / i.compress_size > 100:
                raise Rejected("ARCHIVE_TOO_LARGE")
        names = set(z.namelist())
        try:
            content_types = z.read("[Content_Types].xml")
        except KeyError:
            raise Rejected("CORRUPT")
        if b"macroEnabled" in content_types or "word/vbaProject.bin" in names:
            raise Rejected("MACRO_DOCUMENT")
        if b"wordprocessingml.document.main+xml" not in content_types:
            kind = ("bảng tính Excel" if b"spreadsheetml" in content_types
                    else "PowerPoint" if b"presentationml" in content_types else "không phải Word")
            raise Rejected("TYPE_MISMATCH", detail=f"nội dung là {kind}")
        if "word/document.xml" not in names:
            raise Rejected("CORRUPT")
        xml = z.read("word/document.xml")

    try:
        import docx  # python-docx: make sure the package really opens
        docx.Document(str(path))
    except Exception:
        raise Rejected("CORRUPT")

    text = " ".join(unescape(m.decode("utf-8", "replace")) for m in _W_T.findall(xml))
    if not text.strip():
        raise Rejected("NO_TEXT")
    breaks = xml.count(b"<w:lastRenderedPageBreak")
    pages = breaks + 1 if breaks else None
    if pages and pages > limits.max_pages:
        raise Rejected("TOO_MANY_PAGES", max_pages=limits.max_pages)
    return Inspection("docx", pages=pages, text_sample=text[:20000])


def inspect_file(path: Path, declared_ext: str, limits: Limits) -> Inspection:
    """Validate a staged upload. Raises Rejected with the reason."""
    ext = declared_ext.lower().lstrip(".")
    if ext == "doc":
        raise Rejected("LEGACY_DOC")
    if ext not in limits.allowed_types:
        raise Rejected("UNSUPPORTED_TYPE")
    size = path.stat().st_size
    if size == 0:
        raise Rejected("EMPTY_FILE")
    if size > limits.max_file_bytes:
        raise Rejected("TOO_LARGE", max_file_mb=int(limits.max_file_mb))
    with open(path, "rb") as f:
        head = f.read(2048)
    kind = sniff(head)
    if ext == "pdf":
        if kind != "pdf":
            detail = {"zip": "nội dung là file Office", "ole": "nội dung là file Office cũ"}.get(kind, "không phải PDF")
            raise Rejected("TYPE_MISMATCH", detail=detail)
        return _inspect_pdf(path, limits)
    if ext == "docx":
        if kind == "ole":
            raise Rejected("ENCRYPTED" if _ole_is_encrypted_ooxml(path) else "LEGACY_DOC")
        if kind != "zip":
            detail = "nội dung là PDF" if kind == "pdf" else "không phải DOCX"
            raise Rejected("TYPE_MISMATCH", detail=detail)
        return _inspect_docx(path, limits)
    raise Rejected("UNSUPPORTED_TYPE")


def ocr_available() -> bool:
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False
