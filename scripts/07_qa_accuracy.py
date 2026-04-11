#!/usr/bin/env python
"""
12_qa_accuracy.py
=================
Đánh giá Retrieval Models vs Ground Truth qua QA Accuracy metrics.

Cơ chế so sánh:
  ground_truth.golden_answer (verbatim từ văn bản gốc)
        ↕  so sánh word-level
  top-k retrieved chunk texts (nối lại) từ mỗi model

Metrics tính cho mỗi query × model:
  Recall@k    = |words(golden) ∩ words(retrieved)| / |words(golden)|
                → "Thông tin trong golden có được lấy về không?"

Output:
  data/eval/qa_accuracy.csv        ← so sánh tổng hợp các model
  data/eval/qa_accuracy_detail.jsonl ← chi tiết từng query × model

Usage:
  python scripts/12_qa_accuracy.py
  python scripts/12_qa_accuracy.py --k 3 5 10 --output data/eval/qa_accuracy.csv
  python scripts/12_qa_accuracy.py --runs data/eval/runs_bge_m3.jsonl --k 5
"""

import argparse
import csv
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

sys.stdout.reconfigure(encoding="utf-8")

# ── Vietnamese normalization ──────────────────────────────────────────────────

try:
    from unidecode import unidecode as _unidecode
    def _strip_diacritics(s: str) -> str:
        return _unidecode(s or "").lower()
except ImportError:
    def _strip_diacritics(s: str) -> str:
        # Fallback: chỉ lowercase, không bỏ dấu
        return (s or "").lower()


VI_STOPWORDS: Set[str] = {
    "la", "va", "cua", "co", "trong", "voi", "theo", "duoc", "ve",
    "cho", "cac", "mot", "khong", "nay", "do", "tu", "khi", "tai",
    "den", "thi", "nhu", "hay", "hoac", "deu", "da", "se", "phai",
    "neu", "ma", "bao", "gom", "tong", "sau", "truoc", "tren", "duoi",
    "boi", "vi", "the", "nao", "gi", "ai", "day", "rat", "se", "bi",
    # Thêm dạng có dấu (dùng khi không strip diacritics)
    "là", "và", "của", "có", "trong", "với", "theo", "được", "về",
    "cho", "các", "một", "không", "này", "đó", "từ", "khi", "tại",
    "đến", "thì", "như", "hay", "hoặc", "đều", "đã", "sẽ", "phải",
}


def normalize_text(text: str, remove_stopwords: bool = False) -> str:
    """
    Normalize cho Exact Match (EM):
    - Lowercase + bỏ dấu câu + collapse whitespace
    - KHÔNG bỏ diacritics tiếng Việt (giữ nguyên "tất cả")
    """
    s = (text or "").lower()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    if remove_stopwords:
        tokens = [t for t in s.split() if t not in VI_STOPWORDS and len(t) >= 2]
        return " ".join(tokens)
    return s


def normalize_quasi(text: str) -> str:
    """
    Normalize cho Quasi Exact Match (QEM):
    - Bỏ dấu tiếng Việt (unidecode) + lowercase + bỏ stopwords (dạng ASCII)
    """
    s = _strip_diacritics(text or "")
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    tokens = [t for t in s.split() if t not in VI_STOPWORDS and len(t) >= 2]
    return " ".join(tokens)


def tokenize(text: str) -> List[str]:
    """Tokenize normalized text thành danh sách từ."""
    return text.split()


# ── data loaders ──────────────────────────────────────────────────────────────

def read_jsonl(path: Path) -> List[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def load_chunk_texts(processed_dir: Path) -> Dict[str, Dict[str, str]]:
    """
    Load tất cả chunk texts → {doc_id: {source_file: text}}.
    Hỗ trợ doc_id trùng giữa các file khác nhau (567 collisions trong corpus).
    """
    doc_texts: Dict[str, Dict[str, str]] = defaultdict(dict)
    for fpath in sorted(processed_dir.glob("*.jsonl")):
        fname = fpath.name
        for row in read_jsonl(fpath):
            did = row.get("doc_id", "")
            if did:
                doc_texts[did][fname] = row.get("text", "")
    return dict(doc_texts)


def load_runs(runs_path: Path) -> Dict[str, List[dict]]:
    """
    Load runs file → {query_id: [sorted by rank]}.
    """
    rows = read_jsonl(runs_path)
    by_q: Dict[str, List[dict]] = defaultdict(list)
    for r in rows:
        by_q[r["query_id"]].append(r)
    for qid in by_q:
        by_q[qid].sort(key=lambda x: x["rank"])
    return dict(by_q)


# ── core metrics ──────────────────────────────────────────────────────────────

def _chunk_overlap(golden_counter: Counter, golden_len: int,
                   chunk_text: str) -> Dict[str, float]:
    """
    Tính overlap giữa golden answer và MỘT chunk đơn lẻ.
    Returns: {precision, recall, f1, common}
    """
    c_tokens  = tokenize(normalize_text(chunk_text))
    c_counter = Counter(c_tokens)
    common    = sum((golden_counter & c_counter).values())
    n_c       = sum(c_counter.values())

    precision = common / n_c       if n_c       > 0 else 0.0
    recall    = common / golden_len if golden_len > 0 else 0.0
    f1 = (2 * precision * recall / (precision + recall)
          if (precision + recall) > 0 else 0.0)
    return {"precision": precision, "recall": recall, "f1": f1, "common": common}


def word_overlap_metrics(
    golden: str,
    retrieved_chunks: List[str],
) -> Dict[str, float]:
    """
    Tính Recall / Precision / F1 ở word level với hai chiến lược:

    Recall (concat):
        Nối tất cả chunks → đo bao nhiêu % golden nằm trong retrieved.
        Recall = |G ∩ R_concat| / |G|
        Dùng concat vì recall cần biết tổng thể coverage.

    Precision & F1 (best-chunk / SQuAD-style):
        Tính riêng cho từng chunk, lấy chunk cho F1 cao nhất.
        Precision = overlap(G, best_chunk) / |best_chunk|
        F1        = max F1 trên từng chunk đơn lẻ
        Lý do: concat làm loãng precision vì denominator phình to.
               Best-chunk tìm chunk tập trung nhất với golden answer.
    """
    g_tokens      = tokenize(normalize_text(golden))
    golden_counter = Counter(g_tokens)
    golden_len     = len(g_tokens)

    if not golden_len or not retrieved_chunks:
        return {"recall": 0.0, "precision": 0.0, "f1": 0.0}

    # ── Recall: concat all chunks ──────────────────────────────────────────────
    all_tokens  = tokenize(normalize_text(" ".join(retrieved_chunks)))
    all_counter = Counter(all_tokens)
    common_all  = sum((golden_counter & all_counter).values())
    recall      = common_all / golden_len

    # ── Precision & F1: best single chunk (SQuAD-style) ───────────────────────
    best_f1        = 0.0
    best_precision = 0.0
    for chunk in retrieved_chunks:
        if not chunk.strip():
            continue
        m = _chunk_overlap(golden_counter, golden_len, chunk)
        if m["f1"] > best_f1:
            best_f1        = m["f1"]
            best_precision = m["precision"]

    return {"recall": recall, "precision": best_precision, "f1": best_f1}


def exact_match(golden: str, retrieved_text: str) -> int:
    """
    EM = 1 nếu normalize(golden) là substring của normalize(retrieved_concat).
    Không yêu cầu toàn bộ retrieved = golden, chỉ cần golden nằm trong retrieved.
    """
    norm_g = normalize_text(golden)
    norm_r = normalize_text(retrieved_text)
    return int(norm_g in norm_r)


def quasi_exact_match(golden: str, retrieved_text: str) -> int:
    """
    QEM = EM sau khi bỏ dấu tiếng Việt + bỏ stopwords.
    Ít nhạy cảm với sự khác biệt font/OCR.
    """
    quasi_g = normalize_quasi(golden)
    quasi_r = normalize_quasi(retrieved_text)
    return int(quasi_g in quasi_r and len(quasi_g) >= 5)


# ── evaluation loop ───────────────────────────────────────────────────────────

def evaluate_model(
    groundtruth: List[dict],
    runs_by_q: Dict[str, List[dict]],
    doc_texts: Dict[str, str],
    model_name: str,
    k: int,
) -> Tuple[Dict[str, float], List[dict]]:
    """
    Tính QA Accuracy metrics cho một model tại cutoff k.

    Returns:
        aggregate : {metric_name: avg_score}
        details   : list of per-query results
    """
    totals = {"recall": 0.0, "precision": 0.0, "f1": 0.0, "em": 0.0, "qem": 0.0}
    details: List[dict] = []
    n_evaluated = 0

    for record in groundtruth:
        qid     = f"Q{record['id']}"
        golden  = record["golden_answer"]
        source_doc_id = record.get("doc_id", "")

        # Lấy top-k doc_ids từ runs
        q_runs = runs_by_q.get(qid, [])
        top_k_runs = q_runs[:k]
        top_k_ids = [r["doc_id"] for r in top_k_runs]

        # Kiểm tra source chunk có trong top-k không (Source Hit)
        source_hit = int(source_doc_id in top_k_ids)

        # Lấy text từng chunk — dùng source_file nếu có để tránh collision
        def _get_text(run_row: dict) -> str:
            did = run_row["doc_id"]
            files = doc_texts.get(did, {})
            if not files:
                return ""
            sf = run_row.get("source_file", "")
            if sf and sf in files:
                return files[sf]
            # fallback: trả về text đầu tiên tìm thấy
            return next(iter(files.values()))

        retrieved_texts = [_get_text(r) for r in top_k_runs]
        retrieved_concat = " ".join(t for t in retrieved_texts if t)

        if not retrieved_concat.strip():
            # Model không trả về kết quả cho query này
            metrics_q = {"recall": 0.0, "precision": 0.0, "f1": 0.0, "em": 0, "qem": 0}
        else:
            # Recall = concat, Precision & F1 = best single chunk
            overlap = word_overlap_metrics(golden, retrieved_texts)
            em  = exact_match(golden, retrieved_concat)
            qem = quasi_exact_match(golden, retrieved_concat)
            metrics_q = {
                "recall":    overlap["recall"],
                "precision": overlap["precision"],
                "f1":        overlap["f1"],
                "em":        em,
                "qem":       qem,
            }

        for key, val in metrics_q.items():
            totals[key] += val
        n_evaluated += 1

        details.append({
            "query_id":      qid,
            "question":      record.get("question", ""),
            "golden_answer": golden,
            "source_doc_id": source_doc_id,
            "top_k_ids":     top_k_ids,
            "source_hit":    source_hit,
            "model":         model_name,
            "k":             k,
            **{f"{key}@{k}": round(val, 4) for key, val in metrics_q.items()},
        })

    n = max(1, n_evaluated)
    aggregate = {f"{key}@{k}": round(val / n, 4) for key, val in totals.items()}
    aggregate["n_evaluated"] = n_evaluated
    aggregate["source_hit_rate@k"] = round(
        sum(d["source_hit"] for d in details) / n, 4
    )

    return aggregate, details


# ── pretty report ─────────────────────────────────────────────────────────────

def print_report(results: List[dict], ks: List[int]) -> None:
    """In bảng so sánh tổng hợp."""
    sep = "═" * 80

    print(f"\n{sep}")
    print(f"  QA Accuracy — Retrieval Models vs Ground Truth")
    print(f"  So sánh: golden_answer (verbatim) ↔ top-k retrieved chunk texts")
    print(sep)

    for k in ks:
        print(f"\n  ── @k={k} {'─'*60}")
        header = f"  {'Model':<35} {'Recall':>7} {'Precision':>10} {'F1':>7} {'EM':>6} {'QEM':>6} {'SrcHit':>7}"
        print(header)
        print(f"  {'─'*75}")
        for row in results:
            if row.get("k") != k:
                continue
            print(
                f"  {row['model']:<35}"
                f" {row.get(f'recall@{k}', 0):>7.4f}"
                f" {row.get(f'precision@{k}', 0):>10.4f}"
                f" {row.get(f'f1@{k}', 0):>7.4f}"
                f" {row.get(f'em@{k}', 0):>6.4f}"
                f" {row.get(f'qem@{k}', 0):>6.4f}"
                f" {row.get('source_hit_rate@k', 0):>7.4f}"
            )


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="QA Accuracy evaluation: Retrieval Models vs Ground Truth"
    )
    parser.add_argument(
        "--groundtruth", default="data/eval/qa_groundtruth.jsonl",
        help="Ground truth file từ 11_build_groundtruth_qa.py"
    )
    parser.add_argument(
        "--processed-dir", default="data/processed",
        help="Thư mục processed chunks (để lấy text từ doc_id)"
    )
    parser.add_argument(
        "--runs-dir", default="data/eval",
        help="Thư mục chứa runs_*.jsonl files"
    )
    parser.add_argument(
        "--runs", nargs="+", default=None,
        help="Chỉ định runs files cụ thể (default: tất cả runs_*.jsonl)"
    )
    parser.add_argument(
        "--k", type=int, nargs="+", default=[3, 5, 10],
        help="Cutoff(s) cho @k metrics (default: 3 5 10)"
    )
    parser.add_argument(
        "--output", default="data/eval/qa_accuracy.csv",
        help="Output CSV (default: data/eval/qa_accuracy.csv)"
    )
    parser.add_argument(
        "--detail", default="data/eval/qa_accuracy_detail.jsonl",
        help="Output chi tiết per-query (default: data/eval/qa_accuracy_detail.jsonl)"
    )
    parser.add_argument(
        "--model-labels", nargs="+", default=None,
        help="Nhãn tên model (theo thứ tự --runs)"
    )
    args = parser.parse_args()

    gt_path       = Path(args.groundtruth)
    processed_dir = Path(args.processed_dir)
    runs_dir      = Path(args.runs_dir)
    output_path   = Path(args.output)
    detail_path   = Path(args.detail)
    ks            = sorted(args.k)

    # ── Load ground truth ──────────────────────────────────────────────────────
    print(f"\n[1/4] Loading ground truth from {gt_path}")
    if not gt_path.exists():
        sys.exit(f"  ERROR: File không tồn tại: {gt_path}\n"
                 f"  Chạy 11_build_groundtruth_qa.py trước.")
    groundtruth = read_jsonl(gt_path)
    print(f"      {len(groundtruth)} Q&A records")

    # Chuẩn hóa query_id format: record['id'] → "Q{id}"
    for r in groundtruth:
        if "id" not in r:
            r["id"] = 0  # fallback

    # ── Load chunk texts ───────────────────────────────────────────────────────
    print(f"\n[2/4] Loading chunk texts from {processed_dir} ...")
    doc_texts = load_chunk_texts(processed_dir)
    total_entries = sum(len(v) for v in doc_texts.values())
    print(f"      {len(doc_texts)} unique doc_ids ({total_entries} total entries across files)")

    # Kiểm tra coverage
    gt_doc_ids = {r.get("doc_id", "") for r in groundtruth}
    found = gt_doc_ids & set(doc_texts.keys())
    missing = gt_doc_ids - set(doc_texts.keys())
    print(f"      Coverage: {len(found)}/{len(gt_doc_ids)} ground truth doc_ids found")
    if missing:
        print(f"      ⚠️  Missing doc_ids: {list(missing)[:5]}")

    # ── Discover runs files ────────────────────────────────────────────────────
    if args.runs:
        runs_files = [Path(r) for r in args.runs]
    else:
        runs_files = sorted(runs_dir.glob("runs_*.jsonl"))

    if not runs_files:
        sys.exit(f"  ERROR: Không tìm thấy runs files trong {runs_dir}")

    print(f"\n[3/4] Found {len(runs_files)} runs files:")
    for f in runs_files:
        print(f"      {f.name}")

    # ── Evaluate ───────────────────────────────────────────────────────────────
    print(f"\n[4/4] Evaluating ...")
    all_agg_rows: List[dict]    = []
    all_detail_rows: List[dict] = []

    for i, runs_path in enumerate(runs_files):
        # Model label
        if args.model_labels and i < len(args.model_labels):
            model_name = args.model_labels[i]
        else:
            # Auto-detect từ filename: runs_bge_m3.jsonl → bge_m3
            model_name = runs_path.stem.replace("runs_", "")

        print(f"  → {model_name} ({runs_path.name}) ...")
        runs_by_q = load_runs(runs_path)

        for k in ks:
            agg, details = evaluate_model(
                groundtruth=groundtruth,
                runs_by_q=runs_by_q,
                doc_texts=doc_texts,
                model_name=model_name,
                k=k,
            )
            agg["model"] = model_name
            agg["runs"]  = runs_path.name
            agg["k"]     = k
            all_agg_rows.append(agg)
            all_detail_rows.extend(details)

    # ── Print report ───────────────────────────────────────────────────────────
    print_report(all_agg_rows, ks)

    # ── Write CSV ──────────────────────────────────────────────────────────────
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Build CSV columns dynamically
    metric_cols = []
    for k in ks:
        metric_cols += [f"recall@{k}", f"precision@{k}", f"f1@{k}",
                        f"em@{k}", f"qem@{k}", "source_hit_rate@k"]

    csv_cols = ["model", "runs", "k", "n_evaluated"] + metric_cols

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=csv_cols, extrasaction="ignore")
        writer.writeheader()
        for row in all_agg_rows:
            writer.writerow(row)

    print(f"  Results → {output_path}")

    # ── Write detail JSONL ─────────────────────────────────────────────────────
    detail_path.parent.mkdir(parents=True, exist_ok=True)
    with open(detail_path, "w", encoding="utf-8") as f:
        for row in all_detail_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

if __name__ == "__main__":
    main()
