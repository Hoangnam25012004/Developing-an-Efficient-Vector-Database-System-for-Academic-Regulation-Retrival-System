#!/usr/bin/env python
"""
01_parse_chunk.py – Parse PDF/DOCX, clean, chunk, emit JSONL.

Group metadata is automatically detected from the parent folder name.
Per-file metadata (title, doc_number, year) is derived from the filename.

Metadata priority (low → high):
  1. cfg["doc_defaults"]       (default.yaml)
  2. GROUP_METADATA[folder]    (group-level constants)
  3. auto_meta                 (derived from filename)
  4. FILE_META_OVERRIDES[stem] (hardcoded per-file overrides)
  5. CLI --title / --version … (highest priority)
"""

import argparse
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from scripts.utils import (
    build_doc_id,
    chunk_by_tokens,
    detect_structure,
    ensure_dir,
    force_item_newlines,
    inject_paragraph_breaks,
    is_table_like,
    list_files_recursively,
    load_config,
    normalize_soft_wrap,
    normalize_vietnamese,
    remove_headers_footers,
    remove_repeated_table_headers,
    save_jsonl,
    text_from_docx,
    text_from_pdf,
    fix_missing_space_after_roman,
    fix_dieu_number_split,
    remove_page_overlaps,
    force_decimal_item_newlines,
    dedupe_consecutive_lines,
)

# ---------------------------------------------------------------------------
# Group metadata – keyed by EXACT folder name (lowercase, as on disk)
# ---------------------------------------------------------------------------
GROUP_METADATA: Dict[str, Dict[str, Any]] = {
    "luat-quoc-gia": {
        "group": "luat_quoc_gia",
        "doc_type": "Luật",
        "issuing_authority": "Quốc hội",
        "scope": "Quốc gia",
        "has_chapter": True,
        # doc_number_pattern: "XX/YYYY/QHXX"
        # structure: Chương → Điều → Khoản → Điểm
    },
    "thong-tu-quy-che-cap-bo-gddt": {
        "group": "thong_tu_bo_gd",
        "doc_type": "Thông tư",
        "issuing_authority": "Bộ Giáo dục và Đào tạo",
        "scope": "Quốc gia",
        "has_chapter": True,
        # pdf_type: mostly scanned – OCR triggered automatically
        # doc_number_pattern: "XX/YYYY/TT-BGDĐT"
    },
    "quyet-dinh-quy-che-cap-dhqg-hcm": {
        "group": "qd_qc_dhqg_hcm",
        "doc_type": "Quyết định",
        "issuing_authority": "ĐHQG-HCM",
        "scope": "ĐHQG-HCM",
        "has_chapter": True,
        # doc_number_pattern: "XXXX/QĐ-ĐHQG"
    },
    "quyet-dinh-quy-che-quy-dinh-day-du-cua-truong-dhqt": {
        "group": "qd_qc_dhqt",
        "doc_type": "Quyết định",
        "issuing_authority": "Hiệu trưởng Trường ĐHQT",
        "scope": "Trường ĐHQT",
        "has_chapter": True,
        # Outer: QĐ (Điều 1-3/4); Inner: Quy chế/Quy định (Chương → Điều → Khoản → Điểm)
        # doc_number_pattern: "XXX/QĐ-ĐHQT"
    },
    "quyet-dinh-ngan-noi-quy-cua-truong-dhqt": {
        "group": "qd_ngan_dhqt",
        "doc_type": "Quyết định",
        "issuing_authority": "Hiệu trưởng Trường ĐHQT",
        "scope": "Trường ĐHQT",
        "has_chapter": False,
        # Short QĐ: Điều only, no Chương; 1-6 pages
        # doc_number_pattern: "XXX/QĐ-ĐHQT"
    },
    "phu-luc": {
        "group": "phu_luc",
        "doc_type": "Phụ lục",
        "issuing_authority": "Trường ĐHQT",
        "scope": "Trường ĐHQT",
        "has_chapter": False,
        # No own doc_number; parent_doc derived per-file
        # structure: Mục / Bảng / Danh sách
    },
    "thong-bao": {
        "group": "thong_bao",
        "doc_type": "Thông báo",
        "issuing_authority": "Phòng Công tác Sinh viên – Trường ĐHQT",
        "scope": "Trường ĐHQT",
        "has_chapter": False,
        # doc_number_pattern: "XXX-TB/DHQT-CTSV"
        # pdf_type: scanned – OCR triggered automatically
    },
}

# ---------------------------------------------------------------------------
# Per-file metadata overrides – for files where auto-detection is insufficient
# Keys are exact file stems (no extension).
# ---------------------------------------------------------------------------
FILE_META_OVERRIDES: Dict[str, Dict[str, Any]] = {
    # --- Group 4: Quy chế CTSV ---
    "Quy-che-CTSV-theo-QD-967-12.2022-Signed-2": {
        "doc_number": "967/QĐ-ĐHQT",
        "effective_date": "2022-12-30",
        "title": "Quy chế Công tác Sinh viên Trường ĐHQT (QĐ 967/2022)",
    },
    # --- Group 5: QĐ ngắn + phụ lục BCS ---
    "272-Phu-luc-Quy-dinh-doi-voi-BCSL": {
        "doc_number": "272/QĐ-ĐHQT",
        "effective_date": "2025-05-13",
        "title": "QĐ 272 – Bổ sung Quy định Ban Cán sự Lớp (2025)",
    },
    "2.-Quyet-dinh-dieu-chinh-Quy-che-CTSV-09_1_25-Signed-4": {
        "effective_date": "2025-01-09",
        "title": "QĐ điều chỉnh Quy chế Công tác Sinh viên (01/2025)",
    },
    # --- Group 4: các QĐ có số rõ ---
    "15.-QD-719-6.12.2021_Quy-che-Hoc-vu-DH-Quoc-te-2021.signed-1.signed.signed.signed.signed-2": {
        "doc_number": "719/QĐ-ĐHQT",
        "effective_date": "2021-12-06",
        "title": "Quy chế Học vụ Trường ĐHQT (QĐ 719/2021)",
    },
    "9.-QD688-MGHP-2024": {
        "doc_number": "688/QĐ-ĐHQT",
        "effective_date": "2024-01-01",
        "title": "QĐ 688 – Quy định Miễn Giảm Học phí (2024)",
    },
    "12.Quy-che-to-chuc-hoat-dong-cua-P.CTSV-theo-QD-515-ngay-8.9.2022": {
        "doc_number": "515/QĐ-ĐHQT",
        "effective_date": "2022-09-08",
        "title": "Quy chế Tổ chức và Hoạt động Phòng CTSV (QĐ 515/2022)",
    },
    # --- Group 3: ĐHQG ---
    "25.-QD1342_220930_QD-ban-hanh-Quy-che-dao-tao-trinh-odo-DH-upload": {
        "doc_number": "1342/QĐ-ĐHQG",
        "effective_date": "2022-09-30",
        "title": "Quy chế Đào tạo trình độ Đại học ĐHQG-HCM (QĐ 1342/2022)",
    },
    "23.-QD1133_22815_VNU_ban-hanh-Quy-dinh-khen-thuong-HSSV-1": {
        "doc_number": "1133/QĐ-ĐHQG",
        "title": "Quy định Khen thưởng HSSV ĐHQG-HCM (QĐ 1133)",
    },
    # --- Group 6: Phụ lục ---
    "3.-Phu-luc-1-30122022-Signed-2": {
        "parent_doc": "967/QĐ-ĐHQT",
        "effective_date": "2022-12-30",
        "title": "Phụ lục I – Quy chế CTSV ĐHQT (kèm QĐ 967/2022)",
    },
    "4.-Phu-luc-II-Tieu-chi-va-Khung-DRL-Signed-3": {
        "parent_doc": "967/QĐ-ĐHQT",
        "title": "Phụ lục II – Tiêu chí và Khung Điểm Rèn luyện",
    },
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_YEAR_RE = re.compile(r"\b(20\d{2}|19\d{2})\b")
_DOC_NUM_RE = re.compile(r"(?:QD|QD)[-_]?(\d+)", re.I)
_SIGNED_SUFFIX_RE = re.compile(
    r"(?i)[._-]+(signed[-_]?\d*|final[-_]?\w*|upload)(\.signed\S*)*$"
)


def get_group_metadata(path: Path) -> Dict[str, Any]:
    """Return group metadata dict based on parent folder name (case-insensitive)."""
    folder = path.parent.name.lower()
    return dict(GROUP_METADATA.get(folder, {}))


def derive_file_title(stem: str) -> str:
    """Convert a file stem to a human-readable title."""
    s = stem
    # Remove signed/final suffixes first (they contain hyphens too)
    s = _SIGNED_SUFFIX_RE.sub("", s)
    # Remove leading numeric index prefix: "26.", "15.-", "272-"
    s = re.sub(r"^\d+[\.\-_]\s*", "", s)
    # Replace hyphens/underscores with spaces
    s = re.sub(r"[-_]+", " ", s)
    # Collapse extra spaces
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s


def extract_year_from_stem(stem: str) -> Optional[str]:
    """Extract the most prominent year from the filename (prefer later years)."""
    hits = _YEAR_RE.findall(stem)
    return hits[-1] if hits else None


def extract_doc_number_from_stem(stem: str) -> Optional[str]:
    """Try to extract a QĐ/TB document number from the filename."""
    # Pattern: QD-719, QD688, QĐ-586, QD1342
    m = re.search(r"(?:QD|Q[DĐ])[-_]?(\d{2,})", stem, re.I)
    if m:
        return m.group(1)
    return None


# ---------------------------------------------------------------------------
# Core processing
# ---------------------------------------------------------------------------
def process_file(path: Path, cfg: dict, meta_overrides: dict) -> List[Dict[str, Any]]:
    print(f"[parse] {path.name}  (group: {path.parent.name})")
    ext = path.suffix.lower()

    if ext == ".pdf":
        full_text, pages = text_from_pdf(str(path))
        pages = remove_headers_footers(pages)
        pages = remove_page_overlaps(pages)

        full_text = ""
        for i, p in enumerate(pages, start=1):
            full_text += f"\n<<<PAGE:{i}>>>\n{(p or '').strip()}\n"
    elif ext == ".docx":
        full_text = text_from_docx(str(path))
    else:
        raise ValueError(f"Unsupported file type: {ext}")

    # Normalize whitespace lightly (keep line structure)
    full_text = "\n".join(line.rstrip() for line in (full_text or "").splitlines())

    # NFC-normalise Vietnamese + strip zero-width chars and U+FFFD watermark residue.
    full_text = normalize_vietnamese(full_text)

    # --- detect table-like docs early (appendix/table PDFs) ---
    lines0 = full_text.splitlines()
    table_mode = is_table_like(lines0)

    # --- soft-wrap normalization ---
    if table_mode:
        full_text = force_decimal_item_newlines(full_text)
        full_text = force_item_newlines(full_text)
        full_text = dedupe_consecutive_lines(full_text)
        full_text = remove_repeated_table_headers(full_text)
    else:
        full_text = normalize_soft_wrap(full_text)

    full_text = force_item_newlines(full_text)
    full_text = fix_missing_space_after_roman(full_text)
    full_text = fix_dieu_number_split(full_text)

    # Inject paragraph breaks around markers
    full_text = inject_paragraph_breaks(full_text)

    lines = full_text.splitlines()

    segs = detect_structure(lines, table_mode=table_mode)

    # Do not drop content before the first detected segment
    if segs and segs[0]["start_idx"] > 0:
        segs = [
            {
                "start_idx": 0,
                "end_idx": segs[0]["start_idx"],
                "path_hierarchy": [],
                "article_no": None,
                "clause_no": None,
                "point": None,
            }
        ] + segs

    if not segs:
        segs = [
            {
                "start_idx": 0,
                "end_idx": len(lines),
                "path_hierarchy": [],
                "article_no": None,
                "clause_no": None,
                "point": None,
            }
        ]

    # -----------------------------------------------------------------------
    # Build merged metadata: defaults < group < per-file-auto < per-file-override < CLI
    # -----------------------------------------------------------------------
    defaults = cfg.get("doc_defaults", {}).copy()

    # 1) Group metadata (from parent folder)
    group_meta = get_group_metadata(path)
    defaults.update(group_meta)

    # 2) Per-file auto-derived metadata
    stem = path.stem
    auto_meta: Dict[str, Any] = {}

    auto_meta["title"] = derive_file_title(stem)
    auto_meta["source_file"] = path.name

    yr = extract_year_from_stem(stem)
    if yr:
        auto_meta["version"] = yr
        auto_meta["effective_date"] = f"{yr}-01-01"

    dn = extract_doc_number_from_stem(stem)
    if dn:
        auto_meta["doc_number"] = dn

    defaults.update(auto_meta)

    # 3) Hardcoded per-file overrides (highest accuracy)
    if stem in FILE_META_OVERRIDES:
        defaults.update(FILE_META_OVERRIDES[stem])

    # 4) CLI overrides (highest priority – allow ad-hoc correction)
    defaults.update(meta_overrides or {})

    # -----------------------------------------------------------------------
    # Chunk & emit rows
    # -----------------------------------------------------------------------
    out_rows: List[Dict[str, Any]] = []
    target_tokens = int(cfg["chunking"]["target_tokens"])
    overlap_ratio = 0.0 if table_mode else float(cfg["chunking"]["overlap_ratio"])

    chunk_idx = 0
    for seg in segs:
        seg_text = "\n".join(
            l for l in lines[seg["start_idx"] : seg["end_idx"]]
            if not l.strip().startswith("<<<PAGE:")
        ).strip()
        if not seg_text:
            continue

        pieces = chunk_by_tokens(
            seg_text,
            target_tokens=target_tokens,
            overlap_ratio=overlap_ratio,
        )

        for piece in pieces:
            doc_id = build_doc_id(
                seg.get("article_no"),
                seg.get("clause_no"),
                seg.get("point"),
                chunk_idx,
            )
            row = {
                # --- Structural (per-chunk) ---
                "doc_id": doc_id,
                "source_file": defaults.get("source_file"),
                "path_hierarchy": seg.get("path_hierarchy", []),
                "article_no": seg.get("article_no"),
                "clause_no": seg.get("clause_no"),
                "point": seg.get("point"),
                "page": seg.get("page"),
                "role": seg.get("role"),
                # --- Document identity ---
                "title": defaults.get("title"),
                "doc_number": defaults.get("doc_number"),
                "doc_type": defaults.get("doc_type"),
                "issuing_authority": defaults.get("issuing_authority"),
                "scope": defaults.get("scope"),
                "group": defaults.get("group"),
                "has_chapter": defaults.get("has_chapter"),
                "parent_doc": defaults.get("parent_doc"),   # Phụ lục only
                # --- Versioning ---
                "version": defaults.get("version"),
                "effective_date": defaults.get("effective_date"),
                # --- Context ---
                "faculty": defaults.get("faculty", "All"),
                "language": defaults.get("language", "vi"),
                # --- Content ---
                "text": piece,
            }
            out_rows.append(row)
            chunk_idx += 1

    print(
        f"  -> {len(out_rows)} chunks  |  table_mode={table_mode}"
        f"  |  doc_type={defaults.get('doc_type')}  |  group={defaults.get('group')}"
    )
    return out_rows


def main():
    parser = argparse.ArgumentParser(
        description="Parse PDF/DOCX, clean, chunk, emit JSONL with group-aware metadata"
    )
    parser.add_argument(
        "inputs", nargs="+",
        help="Files or directories containing PDFs/DOCX"
    )
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--title", default=None)
    parser.add_argument("--version", default=None)
    parser.add_argument("--effective_date", default=None)
    parser.add_argument("--faculty", default=None)
    parser.add_argument("--language", default=None)
    parser.add_argument(
        "--out", default=None,
        help="Output JSONL path; default uses data/processed/<stem>.jsonl per file",
    )

    args = parser.parse_args()
    cfg = load_config(args.config)

    meta_overrides = {
        k: v
        for k, v in {
            "title": args.title,
            "version": args.version,
            "effective_date": args.effective_date,
            "faculty": args.faculty,
            "language": args.language,
        }.items()
        if v is not None
    }

    files = list_files_recursively(args.inputs)
    if not files:
        raise SystemExit("No input files found.")

    out_dir = (
        ensure_dir(cfg["paths"]["processed_dir"])
        if args.out is None
        else ensure_dir(Path(args.out).parent)
    )

    total_chunks = 0
    errors = []

    for f in files:
        try:
            rows = process_file(f, cfg, meta_overrides)
            out_path = Path(args.out) if args.out else (out_dir / f"{f.stem}.jsonl")
            save_jsonl(rows, out_path)
            total_chunks += len(rows)
            print(f"  [ok] {out_path.name}  ({len(rows)} chunks)\n")
        except Exception as e:
            print(f"  [ERROR] {f.name}: {e}\n")
            errors.append((f.name, str(e)))

    print("=" * 60)
    print(f"Done: {len(files) - len(errors)}/{len(files)} files  |  {total_chunks} total chunks")
    if errors:
        print(f"\nFailed files ({len(errors)}):")
        for name, err in errors:
            print(f"  {name}: {err}")


if __name__ == "__main__":
    main()
