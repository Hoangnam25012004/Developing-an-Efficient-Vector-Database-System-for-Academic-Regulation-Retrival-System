#!/usr/bin/env python
import argparse
import json
from pathlib import Path
from typing import Dict, List, Tuple

from scripts.utils import load_config, list_files_recursively, looks_garbled

# NOTE: _extract_text_pdf_default là "private" (underscore) nhưng vẫn import được.
# Mục tiêu: mô phỏng đúng bước parse thường (không OCR) để quyết định fallback.
from scripts.utils import _extract_text_pdf_default  # type: ignore


def classify_reason(full_text: str, min_chars: int) -> str:
    """
    Classify trigger reason for OCR fallback (by file).
    Keep it simple + explainable for reporting.
    """
    t = (full_text or "").strip()
    n = len(t)

    # Gần như không có text layer
    if n == 0 or n < max(30, int(min_chars * 0.10)):
        return "no_text_layer"

    # Có text nhưng quá ít (mật độ thấp)
    if n < min_chars:
        return "low_text_density"

    # Text bị lỗi/garbled (font lỗi/encoding lỗi/ ký tự rác...)
    return "garbled_or_font_error"


def pct(n: int, d: int) -> float:
    return (n / d * 100.0) if d else 0.0


def scan_pdfs(pdf_paths: List[Path], min_chars: int) -> Tuple[Dict, List[Dict]]:
    total = 0
    fallback = 0
    reasons: Dict[str, int] = {}
    details: List[Dict] = []

    for p in pdf_paths:
        total += 1

        full_text, pages = _extract_text_pdf_default(str(p))
        full_text = (full_text or "").strip()

        need_fallback = (len(full_text) < min_chars) or looks_garbled(full_text)
        reason = None

        if need_fallback:
            fallback += 1
            reason = classify_reason(full_text, min_chars)
            reasons[reason] = reasons.get(reason, 0) + 1

        details.append(
            {
                "file": str(p),
                "pages": len(pages or []),
                "chars_extracted": len(full_text),
                "fallback": bool(need_fallback),
                "reason": reason,
            }
        )

    # Build distribution with percentages
    reason_dist = {
        k: {"count": v, "pct_of_fallback": pct(v, fallback)}
        for k, v in sorted(reasons.items(), key=lambda x: (-x[1], x[0]))
    }

    report = {
        "min_chars_threshold": min_chars,
        "total_pdf_files": total,
        "fallback_files": fallback,
        "fallback_rate_files_pct": pct(fallback, total),
        "trigger_reason_distribution": reason_dist,
    }
    return report, details


def main():
    parser = argparse.ArgumentParser(
        description="Compute OCR fallback metrics (by file) + trigger reason distribution without modifying existing code."
    )
    parser.add_argument(
        "inputs",
        nargs="*",
        help="Files or directories containing PDFs. If empty, use config paths (raw_dir) if available.",
    )
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument(
        "--min_chars",
        type=int,
        default=300,
        help="Same MIN_CHARS threshold as utils.text_from_pdf() (default: 300).",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Optional output JSON path. If provided, writes full report (including per-file details).",
    )
    parser.add_argument(
        "--no_details",
        action="store_true",
        help="If set, output report only (no per-file details) to stdout / file.",
    )

    args = parser.parse_args()
    cfg = load_config(args.config)

    # Resolve inputs: if none provided, try cfg["paths"]["raw_dir"] (common pattern)
    in_paths = args.inputs
    if not in_paths:
        raw_dir = None
        try:
            raw_dir = cfg.get("paths", {}).get("raw_dir")
        except Exception:
            raw_dir = None

        if raw_dir:
            in_paths = [raw_dir]
        else:
            raise SystemExit(
                "No inputs provided and config.paths.raw_dir not found. Provide inputs (file/dir) or add raw_dir to config."
            )

    all_files = list_files_recursively(in_paths)
    pdfs = [p for p in all_files if p.suffix.lower() == ".pdf"]
    if not pdfs:
        raise SystemExit("No PDF files found in inputs.")

    report, details = scan_pdfs(pdfs, args.min_chars)

    payload = report if args.no_details else {**report, "details": details}

    # stdout
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    # optional file output
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
