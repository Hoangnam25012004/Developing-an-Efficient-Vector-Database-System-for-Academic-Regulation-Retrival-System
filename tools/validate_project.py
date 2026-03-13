#!/usr/bin/env python3
import argparse
import codecs
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

RE_CHAPTER = re.compile(r"\b(Chương|Chuong)\b", re.IGNORECASE)
RE_ARTICLE = re.compile(r"\b(Điều|Dieu)\s*\d+\b", re.IGNORECASE)
RE_CLAUSE  = re.compile(r"\b(Khoản|Khoan)\s*\d+\b", re.IGNORECASE)
RE_POINT   = re.compile(r"\b(Điểm|Diem)\s*[a-z]\b", re.IGNORECASE)

REQUIRED_SCRIPTS = [
    "scripts/01_parse_chunk.py",
    "scripts/02_embed_ingest.py",
    "scripts/03_search_dense.py",
    "scripts/04_search_sparse.py",
    "scripts/05_hybrid_rrf.py",
    "scripts/06_rerank.py",
    "scripts/07_eval_metrics.py",
    "scripts/utils.py",
]

REQUIRED_FILES = [
    ".gitignore",
    ".pre-commit-config.yaml",
    ".flake8",
    "requirements.txt",
    "README.md",
]

# các gói trọng yếu (chỉ check xuất hiện trong requirements.txt)
REQUIRED_PACKAGES = [
    # Core / parsing
    "regex", "rapidfuzz", "pdfminer.six", "pdfplumber", "pypdf",
    # tokenization/embeddings
    "tiktoken", "sentence-transformers", "transformers", "torch",
    # vector & sparse
    "qdrant-client", "rank-bm25",
    # API/UI
    "fastapi", "uvicorn", "pydantic", "streamlit",
    # eval
    "scikit-learn",
]

def ok(msg):  print(f"✅ {msg}")
def warn(msg): print(f"⚠️  {msg}")
def fail(msg): print(f"❌ {msg}")

def has_bom(path: Path) -> bool:
    if not path.exists(): return False
    head = path.read_bytes()[:3]
    return head.startswith(codecs.BOM_UTF8)

def check_repo_structure(root: Path):
    print("\n== Repo structure ==")
    all_ok = True
    for f in REQUIRED_FILES:
        p = root / f
        if p.exists():
            ok(f"Found {f}")
        else:
            all_ok = False
            fail(f"Missing {f}")
    for s in REQUIRED_SCRIPTS:
        p = root / s
        if p.exists():
            ok(f"Found {s}")
        else:
            all_ok = False
            fail(f"Missing {s}")
    # data
    data_dir = root / "data"
    if data_dir.exists():
        ok("Found data/ (good for input/output)")
    else:
        warn("Not found data/ (khuyến nghị tạo data/raw, data/parsed)")
    return all_ok

def check_flake8(root: Path):
    print("\n== Flake8 config ==")
    cfg = root / ".flake8"
    if not cfg.exists():
        fail("Missing .flake8")
        return False
    if has_bom(cfg):
        fail(".flake8 đang có BOM (UTF-8 BOM). Hãy lưu lại no-BOM.")
        return False
    # Đọc bằng configparser-like đơn giản
    text = cfg.read_text(encoding="utf-8", errors="ignore")
    if "[flake8]" not in text:
        fail("'.flake8' không có [flake8] section")
        return False
    checks = {
        "max-line-length": "100",
        "extend-ignore": ("E203", "W503"),
        "per-file-ignores": ("api/main.py:E402,F811",),
    }
    ok_all = True
    for key, expects in checks.items():
        if key not in text:
            fail(f"Thiếu '{key}' trong .flake8")
            ok_all = False
            continue
        if isinstance(expects, tuple):
            for e in expects:
                if e not in text:
                    fail(f".flake8: '{key}' chưa gồm '{e}'")
                    ok_all = False
        else:
            if expects not in text:
                warn(f".flake8: '{key}' khác mong đợi (tìm '{expects}')")
    # exclude
    if "exclude" not in text:
        warn("Nên có 'exclude = .venv, venv, __pycache__, .git, ...' để tránh quét site-packages")
    else:
        ok("Có 'exclude' (tốt)")
    return ok_all

def check_precommit(root: Path):
    print("\n== pre-commit ==")
    pc = root / ".pre-commit-config.yaml"
    if not pc.exists():
        fail("Missing .pre-commit-config.yaml")
        return False
    content = pc.read_text(encoding="utf-8", errors="ignore")
    needed = ["psf/black", "pycqa/isort", "pycqa/flake8", "pre-commit/pre-commit-hooks"]
    ok_all = True
    for k in needed:
        if k in content:
            ok(f"pre-commit includes {k}")
        else:
            ok_all = False
            fail(f"pre-commit missing {k}")
    # thử chạy pre-commit --version
    try:
        out = subprocess.run(["pre-commit", "--version"], capture_output=True, text=True, check=False)
        if out.returncode == 0:
            ok(f"pre-commit OK: {out.stdout.strip()}")
        else:
            warn("pre-commit chưa sẵn sàng trong PATH (không bắt buộc).")
    except FileNotFoundError:
        warn("pre-commit chưa cài/không trong PATH (không bắt buộc để chạy validate).")
    return ok_all

def check_requirements(root: Path):
    print("\n== requirements.txt ==")
    req = root / "requirements.txt"
    if not req.exists():
        fail("Missing requirements.txt")
        return False
    text = req.read_text(encoding="utf-8", errors="ignore")
    ok_all = True
    for pkg in REQUIRED_PACKAGES:
        if re.search(rf"(?i)^{re.escape(pkg)}\b", text, flags=re.MULTILINE):
            ok(f"{pkg} pinned")
        else:
            warn(f"{pkg} chưa thấy trong requirements.txt")
            ok_all = False
    return ok_all

def tokenize_simple(s: str):
    # dùng split đơn giản để ước lượng token
    return re.findall(r"\w+|\S", s)

def load_any_jsonl(chunks_path: Path | None, root: Path):
    if chunks_path and chunks_path.exists():
        return chunks_path
    # auto-detect
    cands = list(root.glob("**/*.jsonl"))
    cands = [c for c in cands if "chunk" in c.stem.lower() or "ingest" in c.stem.lower()]
    return cands[0] if cands else None

def check_chunks(root: Path, chunks_path: Path | None):
    print("\n== JSONL chunks ==")
    p = load_any_jsonl(chunks_path, root)
    if not p:
        warn("Không tìm thấy file *.jsonl chứa chunks (ví dụ data/chunks.jsonl). Bỏ qua phần kiểm schema.")
        return False
    ok(f"Dùng file: {p.relative_to(root)}")
    required_keys = {"id", "title", "path_hierarchy", "article_no", "clause_no", "text"}
    n = 0
    bad_schema = 0
    lens = []
    overlaps_est = []
    prev_tokens = None
    same_section_prev = None
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line: continue
            n += 1
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                bad_schema += 1
                continue
            if not required_keys.issubset(obj.keys()):
                bad_schema += 1
                continue
            text = obj.get("text", "")
            toks = tokenize_simple(text)
            lens.append(len(toks))
            # overlap xấp xỉ theo Jaccard window đầu/cuối 40 tokens
            if prev_tokens is not None and same_section_prev == (obj.get("article_no"), obj.get("clause_no")):
                head = set(toks[:40])
                tail_prev = set(prev_tokens[-40:])
                if head and tail_prev:
                    j = len(head & tail_prev) / len(head | tail_prev)
                    overlaps_est.append(j)
            prev_tokens = toks
            same_section_prev = (obj.get("article_no"), obj.get("clause_no"))
    if n == 0:
        fail("File JSONL trống.")
        return False
    if bad_schema:
        fail(f"{bad_schema}/{n} dòng không đạt schema {sorted(list(required_keys))}")
    else:
        ok("Schema JSONL hợp lệ cho tất cả entries")

    # thống kê độ dài chunk
    import statistics as stats
    p50 = int(stats.median(lens))
    p10 = int(stats.quantiles(lens, n=10)[0]) if len(lens) >= 10 else min(lens)
    p90 = int(stats.quantiles(lens, n=10)[-1]) if len(lens) >= 10 else max(lens)
    print(f"Độ dài chunk (ước lượng tokens) – p10: {p10}, p50: {p50}, p90: {p90}")
    if 150 <= p50 <= 300:
        ok("Median chunk nằm trong [150, 300] tokens")
    else:
        warn("Median chunk **không** nằm trong [150, 300] tokens")

    # overlap ước lượng ~ 0.2–0.3
    if overlaps_est:
        p50o = sum(overlaps_est)/len(overlaps_est)
        print(f"Ước lượng overlap (Jaccard đầu/cuối 40 tokens): ~{p50o:.2f}")
        if 0.15 <= p50o <= 0.35:
            ok("Overlap nằm ~20–30% (±)")
        else:
            warn("Overlap **không** ở mức 20–30% (±)")
    else:
        warn("Không đủ dữ liệu để ước lượng overlap (có thể chunk theo file, không theo section).")
    return True

def check_text_structure(root: Path):
    print("\n== Cấu trúc văn bản (regex) ==")
    # Tìm một số file text/parsed để sanity-check
    candidates = list(root.glob("data/**/parsed/**/*.txt")) + list(root.glob("data/**/parsed/*.txt"))
    if not candidates:
        candidates = list(root.glob("data/**/*.txt"))
    if not candidates:
        warn("Không tìm thấy *.txt để kiểm tra cấu trúc. Bỏ qua.")
        return False
    sample = candidates[0]
    text = Path(sample).read_text(encoding="utf-8", errors="ignore")
    found = {
        "Chương": bool(RE_CHAPTER.search(text)),
        "Điều": bool(RE_ARTICLE.search(text)),
        "Khoản": bool(RE_CLAUSE.search(text)),
        "Điểm": bool(RE_POINT.search(text)),
    }
    for k, v in found.items():
        if v: ok(f"Tìm thấy mẫu '{k}' trong văn bản")
        else: warn(f"Không thấy mẫu '{k}' – kiểm tra lại bước chuẩn hoá/regex")

    return any(found.values())

def main():
    ap = argparse.ArgumentParser(description="Validate AcademicRegulation ingestion prototype")
    ap.add_argument("--chunks", type=Path, default=None, help="Đường dẫn file JSONL chunks (nếu để trống script sẽ tự dò).")
    args = ap.parse_args()
    root = Path.cwd()

    print("=== VALIDATION START ===")

    s1 = check_repo_structure(root)
    s2 = check_flake8(root)
    s3 = check_precommit(root)
    s4 = check_requirements(root)
    s5 = check_chunks(root, args.chunks)
    s6 = check_text_structure(root)

    print("\n=== SUMMARY ===")
    results = {
        "structure": s1,
        "flake8": s2,
        "precommit": s3,
        "requirements": s4,
        "chunks": s5,
        "text_structure": s6,
    }
    for k, v in results.items():
        print(f"- {k}: {'OK' if v else 'CHECK'}")

    # exit code !=0 nếu có mục chưa đạt
    if all(results.values()):
        ok("Project đáp ứng yêu cầu đề bài ✅")
        sys.exit(0)
    else:
        fail("Còn mục cần chỉnh. Xem cảnh báo ở trên.")
        sys.exit(1)

if __name__ == "__main__":
    main()
