#!/usr/bin/env python
import argparse
from pathlib import Path
from typing import Any, Dict, List

from utils import (build_doc_id, chunk_by_tokens, detect_structure, ensure_dir,
                   list_files_recursively, load_config, remove_headers_footers,
                   save_jsonl, text_from_docx, text_from_pdf, vnfold)


def process_file(path: Path, cfg: dict, meta_overrides: dict) -> List[Dict[str, Any]]:
    print(f"[parse] {path}")
    ext = path.suffix.lower()
    pages_text = []
    full_text = ""
    if ext == ".pdf":
        full_text, pages = text_from_pdf(str(path))
        pages = remove_headers_footers(pages)
        full_text = "\n\n".join(pages)
    elif ext == ".docx":
        full_text = text_from_docx(str(path))
    else:
        raise ValueError(f"Unsupported file type: {ext}")

    # Normalize whitespace
    full_text = "\n".join(line.rstrip() for line in full_text.splitlines())

    lines = full_text.splitlines()
    segs = detect_structure(lines)

    if not segs:
        # No structural headings found; treat entire doc as one segment
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

    out_rows = []
    target_tokens = cfg["chunking"]["target_tokens"]
    overlap_ratio = cfg["chunking"]["overlap_ratio"]

    defaults = cfg.get("doc_defaults", {})
    defaults.update(meta_overrides or {})

    chunk_idx = 0
    for seg in segs:
        text = "\n".join(lines[seg["start_idx"] : seg["end_idx"]]).strip()
        if not text:
            continue
        pieces = chunk_by_tokens(
            text, target_tokens=target_tokens, overlap_ratio=overlap_ratio
        )
        for piece in pieces:
            doc_id = build_doc_id(
                seg.get("article_no"), seg.get("clause_no"), seg.get("point"), chunk_idx
            )
            row = {
                "doc_id": doc_id,
                "title": defaults.get("title"),
                "path_hierarchy": seg.get("path_hierarchy", []),
                "article_no": seg.get("article_no"),
                "clause_no": seg.get("clause_no"),
                "point": seg.get("point"),
                "version": defaults.get("version"),
                "effective_date": defaults.get("effective_date"),
                "faculty": defaults.get("faculty"),
                "language": defaults.get("language", "vi"),
                "text": piece,
            }
            out_rows.append(row)
            chunk_idx += 1
    return out_rows


def main():
    parser = argparse.ArgumentParser(
        description="Parse PDF/DOCX, clean, chunk, emit JSONL"
    )
    parser.add_argument(
        "inputs", nargs="+", help="Files or directories containing PDFs/DOCX"
    )
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--title", default=None)
    parser.add_argument("--version", default=None)
    parser.add_argument("--effective_date", default=None)
    parser.add_argument("--faculty", default=None)
    parser.add_argument("--language", default=None)
    parser.add_argument(
        "--out",
        default=None,
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

    for f in files:
        rows = process_file(f, cfg, meta_overrides)
        if args.out:
            out_path = Path(args.out)
        else:
            out_path = out_dir / f"{f.stem}.jsonl"
        save_jsonl(rows, out_path)
        print(f"[ok] wrote {out_path} ({len(rows)} chunks)")


if __name__ == "__main__":
    main()
