# reg-retrieval — Hệ thống truy xuất văn bản pháp quy học thuật tại trường đại học Quốc Tế- đại học Quốc Gia TPHCM

Pipeline end-to-end để tìm kiếm thông minh trong các văn bản quy chế, quyết định, thông tư của trường đại học và bộ ngành.

```
PDF/DOCX → parse → chunk → embed → index → search → rerank → API + UI
```

---

## Mục lục

1. [Tổng quan kiến trúc](#1-tổng-quan-kiến-trúc)
2. [Yêu cầu hệ thống](#2-yêu-cầu-hệ-thống)
3. [Cài đặt môi trường](#3-cài-đặt-môi-trường)
4. [Chạy pipeline từ đầu](#4-chạy-pipeline-từ-đầu)
5. [Chạy API và giao diện](#5-chạy-api-và-giao-diện)
6. [Đánh giá và so sánh mô hình](#6-đánh-giá-và-so-sánh-mô-hình)
7. [Cấu trúc dữ liệu](#7-cấu-trúc-dữ-liệu)
8. [Cấu hình](#8-cấu-hình)
9. [Xử lý sự cố](#9-xử-lý-sự-cố)

---

## 1. Tổng quan kiến trúc

```
data/raw/
├── Luat-quoc-gia/                        ← Luật Quốc hội
├── Thong-tu-quy-che-cap-bo-gddt/         ← Thông tư Bộ GD-ĐT
├── Quyet-dinh-quy-che-cap-dhqg-hcm/      ← Quyết định ĐHQG-HCM
├── Quyet-dinh-quy-che-quy-dinh-day-du-cua-truong-dhqt/   ← Quy chế ĐHQT
├── Quyet-dinh-ngan-noi-quy-cua-truong-dhqt/              ← Nội quy ngắn
├── Thong-bao/                            ← Thông báo
└── Phu-luc/                              ← Phụ lục

          [01] parse + chunk
               ↓
        data/processed/*.jsonl
               ↓
    ┌──────────┴──────────┐
  [02] embed            [04] BM25
  (Qdrant)              (bm25.pkl)
    └──────────┬──────────┘
             [05] hybrid RRF
               ↓
             [06] rerank
               ↓
        API (FastAPI :8000)
        UI  (Streamlit :8501)
```

**Các chế độ tìm kiếm:**

| Mode | Mô tả |
|------|-------|
| `dense` | Vector search (multilingual-e5-base) qua Qdrant |
| `sparse` | BM25 trên văn bản đã chuẩn hoá tiếng Việt |
| `hybrid` | Kết hợp dense + sparse bằng Reciprocal Rank Fusion |
| `hybrid_rerank` | Hybrid + cross-encoder reranking |

---

## 2. Yêu cầu hệ thống

| Thành phần | Phiên bản tối thiểu |
|-----------|---------------------|
| Python | 3.8+ |
| Docker | 20.10+ (để chạy Qdrant) |
| RAM | 8 GB (16 GB khuyến nghị khi dùng LLM reranker) |
| Disk | ~3 GB (model + index + data) |

> **Tesseract OCR** (tuỳ chọn): Chỉ cần thiết nếu xử lý PDF scan. Tải tại [github.com/UB-Mannheim/tesseract](https://github.com/UB-Mannheim/tesseract/wiki). Sau khi cài, cập nhật đường dẫn trong `scripts/utils.py` (dòng `DEFAULT_TESSERACT_CMD`).

---

## 3. Cài đặt môi trường

Tất cả lệnh chạy từ **thư mục gốc của project**.

### Bước 1 — Tạo virtual environment

```bash
# Linux / macOS
python -m venv .venv
source .venv/bin/activate

# Windows (PowerShell)
python -m venv .venv
.venv\Scripts\Activate.ps1
```

### Bước 2 — Cài dependencies

```bash
pip install -U pip
pip install -r requirements.txt
```

### Bước 3 — Khởi động Qdrant (vector database)

```bash
docker compose up -d qdrant
```

Kiểm tra Qdrant đã chạy:

```bash
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
# Kết quả mong đợi: qdrant   Up X seconds   0.0.0.0:6333->6333/tcp
```

Dashboard Qdrant: [http://localhost:6333/dashboard](http://localhost:6333/dashboard)

### Bước 4 — Thiết lập PYTHONPATH

```bash
# Linux / macOS
export PYTHONPATH=$(pwd)

# Windows (PowerShell)
$env:PYTHONPATH = (Get-Location).Path

# Windows (CMD)
set PYTHONPATH=%CD%
```

> **Lưu ý**: Cần set `PYTHONPATH` mỗi khi mở terminal mới, hoặc thêm vào file cấu hình shell của bạn.

---

## 4. Chạy pipeline từ đầu

Thực hiện tuần tự các bước sau. Mỗi bước phụ thuộc vào output của bước trước.

### Bước 1 — Parse và chunk văn bản

Đọc tất cả PDF/DOCX trong `data/raw/`, làm sạch văn bản, phát hiện cấu trúc (Chương → Điều → Khoản → Điểm), và chia thành các chunk ~320 token.

```bash
python -m scripts.01_parse_chunk data/raw --config configs/default.yaml
```

Output: `data/processed/*.jsonl` (mỗi dòng là một chunk kèm metadata đầy đủ)

Kiểm tra kết quả:

```bash
# Xem số chunk được tạo
wc -l data/processed/*.jsonl

# Xem thử một chunk
head -n 1 data/processed/*.jsonl | python -m json.tool
```

> **Parse lại từ đầu** (khi cần):
> ```bash
> rm -rf data/processed && mkdir data/processed
> python -m scripts.01_parse_chunk data/raw --config configs/default.yaml
> ```

---

### Bước 2 — Tạo embeddings và đưa vào Qdrant

Mã hoá toàn bộ chunk bằng `intfloat/multilingual-e5-base` (768 chiều) và nạp vào Qdrant.

```bash
python -m scripts.02_embed_ingest --config configs/default.yaml
```

> Lần đầu sẽ tải model ~1.1 GB từ HuggingFace. Các lần sau dùng cache.

Kiểm tra collection trong Qdrant:

```bash
curl http://localhost:6333/collections/reg_chunks
```

---

### Bước 3 — Xây dựng chỉ mục BM25

Tạo chỉ mục BM25 (sparse retrieval) từ các chunk đã được tokenise theo chuẩn tiếng Việt.

```bash
python -m scripts.04_search_sparse build --config configs/default.yaml
```

Output: `data/indexes/bm25.pkl`

---

### Kiểm tra nhanh từng chế độ tìm kiếm

Sau khi hoàn thành 3 bước trên, thử tìm kiếm ngay trên terminal:

```bash
# Dense search
python -m scripts.03_search_dense \
  --config configs/default.yaml \
  --query "sinh viên bị buộc thôi học vì lý do gì" \
  --top_k 5

# Sparse search (BM25)
python -m scripts.04_search_sparse search \
  --config configs/default.yaml \
  --query "sinh viên bị buộc thôi học vì lý do gì" \
  --top_k 5

# Hybrid (RRF)
python -m scripts.05_hybrid_rrf \
  --config configs/default.yaml \
  --query "sinh viên bị buộc thôi học vì lý do gì" \
  --top_k 10

# Hybrid + rerank
python -m scripts.06_rerank \
  --config configs/default.yaml \
  --query "sinh viên bị buộc thôi học vì lý do gì" \
  --top_k 30 \
  --final_k 10
```

---

## 5. Chạy API và giao diện

### Chạy API (FastAPI)

```bash
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

Hoặc dùng entry point:

```bash
python serve.py
```

Swagger UI: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

**Ví dụ gọi API:**

```bash
# Hybrid rerank (mặc định)
curl "http://127.0.0.1:8000/search?query=miễn+giảm+học+phí+điều+kiện+gì&mode=hybrid_rerank&top_k=5"

# Dense only
curl "http://127.0.0.1:8000/search?query=miễn+giảm+học+phí&mode=dense&top_k=10"
```

**Các mode hợp lệ:** `dense` | `sparse` | `hybrid` | `hybrid_rerank`

### Chạy giao diện Streamlit

Mở terminal mới (giữ API đang chạy):

```bash
streamlit run ui/app.py
```

Truy cập: [http://localhost:8501](http://localhost:8501)

---

## 6. Đánh giá và so sánh mô hình

### Đánh giá một phương pháp

Chạy tìm kiếm trên tập 350 câu hỏi chuẩn và tính các chỉ số Recall, Precision, MAP, nDCG, MRR:

```bash
# Tạo kết quả chạy (ví dụ: hybrid_rerank)
python -m scripts.06_rerank \
  --config configs/default.yaml \
  --queries data/eval/queries.jsonl \
  --output data/eval/runs_bge_m3.jsonl

# Đánh giá
python -m scripts.07_eval_metrics \
  --queries data/eval/queries.jsonl \
  --qrels   data/eval/qrels.jsonl \
  --runs    data/eval/runs_bge_m3.jsonl \
  --k 10
```

### So sánh nhiều reranker

Lặp lại quy trình: sửa `model_name` trong `configs/default.yaml`, chạy rerank, đánh giá và ghi kết quả vào CSV.

**Model 1 — BAAI/bge-reranker-v2-m3** (mặc định, không cần GPU)
```bash
# default.yaml đã cấu hình sẵn: model_name: "BAAI/bge-reranker-v2-m3"
python -m scripts.06_rerank --queries data/eval/queries.jsonl --output data/eval/runs_bge_m3.jsonl
python -m scripts.07_eval_metrics \
  --queries data/eval/queries.jsonl --qrels data/eval/qrels.jsonl \
  --runs data/eval/runs_bge_m3.jsonl --model "BAAI/bge-reranker-v2-m3" \
  --output data/eval/comparison.csv
```

**Model 2 — jinaai/jina-reranker-v2-base-multilingual**
```bash
# Sửa configs/default.yaml: model_name: "jinaai/jina-reranker-v2-base-multilingual"
python -m scripts.06_rerank --queries data/eval/queries.jsonl --output data/eval/runs_jina.jsonl
python -m scripts.07_eval_metrics \
  --queries data/eval/queries.jsonl --qrels data/eval/qrels.jsonl \
  --runs data/eval/runs_jina.jsonl --model "jinaai/jina-reranker-v2-base-multilingual" \
  --output data/eval/comparison.csv
```

**Model 3 — itdainb/PhoRanker** (tiếng Việt)
```bash
# Sửa configs/default.yaml: model_name: "itdainb/PhoRanker"
python -m scripts.06_rerank --queries data/eval/queries.jsonl --output data/eval/runs_phoranker.jsonl
python -m scripts.07_eval_metrics \
  --queries data/eval/queries.jsonl --qrels data/eval/qrels.jsonl \
  --runs data/eval/runs_phoranker.jsonl --model "itdainb/PhoRanker" \
  --output data/eval/comparison.csv
```

**Model 4 — BAAI/bge-reranker-v2-gemma** (cần GPU + FlagEmbedding)
```bash
pip install FlagEmbedding
# Sửa configs/default.yaml: model_name: "BAAI/bge-reranker-v2-gemma", use_fp16: true
python -m scripts.06_rerank --queries data/eval/queries.jsonl --output data/eval/runs_bge_gemma.jsonl
python -m scripts.07_eval_metrics \
  --queries data/eval/queries.jsonl --qrels data/eval/qrels.jsonl \
  --runs data/eval/runs_bge_gemma.jsonl --model "BAAI/bge-reranker-v2-gemma" \
  --output data/eval/comparison.csv
```

Kết quả so sánh lưu tại `data/eval/comparison.csv`.

---

## 7. Cấu trúc dữ liệu

### Cây thư mục

```
reg-retrieval/
├── api/                    # FastAPI backend
│   └── main.py
├── configs/
│   └── default.yaml        # Cấu hình toàn bộ pipeline
├── data/
│   ├── raw/                # PDF/DOCX đầu vào (thêm tài liệu mới vào đây)
│   │   ├── Luat-quoc-gia/
│   │   ├── Thong-tu-quy-che-cap-bo-gddt/
│   │   ├── Quyet-dinh-quy-che-cap-dhqg-hcm/
│   │   ├── Quyet-dinh-quy-che-quy-dinh-day-du-cua-truong-dhqt/
│   │   ├── Quyet-dinh-ngan-noi-quy-cua-truong-dhqt/
│   │   ├── Thong-bao/
│   │   └── Phu-luc/
│   ├── processed/          # Chunks JSONL (auto-generated)
│   ├── indexes/            # BM25 index (auto-generated)
│   │   └── bm25.pkl
│   └── eval/               # Dữ liệu đánh giá
│       ├── queries.jsonl   # 350 câu hỏi kiểm tra
│       └── qrels.jsonl     # Ground truth relevance
├── scripts/                # Pipeline scripts
│   ├── 01_parse_chunk.py
│   ├── 02_embed_ingest.py
│   ├── 03_search_dense.py
│   ├── 04_search_sparse.py
│   ├── 05_hybrid_rrf.py
│   ├── 06_rerank.py
│   ├── 07_eval_metrics.py
│   └── utils.py
├── ui/
│   └── app.py              # Streamlit UI
├── docker-compose.yml      # Qdrant container
├── requirements.txt
└── serve.py                # Entry point cho API
```

### Schema một chunk (data/processed/*.jsonl)

```jsonc
{
  "doc_id": "thong_tu_bo_gd__27-2023_chu_4_dieu_15_khoan_2",
  "source_file": "Thong-tu-27-2023.pdf",
  "group": "thong_tu_bo_gd",
  "doc_type": "Thông tư",
  "issuing_authority": "Bộ GD-ĐT",
  "scope": "Quốc gia",
  "doc_number": "27/2023/TT-BGDĐT",
  "path_hierarchy": "Chương IV > Điều 15 > Khoản 2",
  "article_no": 15,
  "clause_no": 2,
  "page": 8,
  "text": "Sinh viên bị buộc thôi học khi..."
}
```

### Format file đánh giá

```jsonc
// queries.jsonl
{"query_id": "Q001", "query": "điều kiện miễn giảm học phí"}

// qrels.jsonl (thang điểm: 0=không liên quan, 1=liên quan, 2=rất liên quan)
{"query_id": "Q001", "doc_id": "thong_tu_27_dieu_5_khoan_1", "relevance": 2}

// runs.jsonl (output của 06_rerank.py)
{"query_id": "Q001", "doc_id": "thong_tu_27_dieu_5_khoan_1", "rank": 1, "score": 0.97}
```

---

## 8. Cấu hình

Tất cả tham số được quản lý trong `configs/default.yaml`:

```yaml
chunking:
  target_tokens: 320      # Kích thước chunk (~150–320 tokens)
  overlap_ratio: 0.08     # Overlap 8% giữa các chunk liền kề

embedding:
  model_name: "intfloat/multilingual-e5-base"   # Model embedding
  normalize: true
  batch_size: 64          # Tăng nếu có GPU mạnh

reranker:
  model_name: "BAAI/bge-reranker-v2-m3"         # Thay để thử model khác
  batch_size: 32
  use_fp16: false         # Bật khi dùng LLM reranker trên GPU

qdrant:
  url: "http://localhost:6333"
  collection: "reg_chunks"

search:
  top_k: 10               # Số kết quả trả về
  rrf_k: 60               # Tham số RRF (càng lớn = ít ưu tiên rank đầu)
```

**Thêm tài liệu mới:** Đặt file PDF/DOCX vào thư mục con tương ứng trong `data/raw/`, sau đó chạy lại từ [Bước 1](#bước-1--parse-và-chunk-văn-bản).

---

## 9. Xử lý sự cố

**Lỗi `ModuleNotFoundError: No module named 'scripts'`**
```bash
# Đảm bảo đã set PYTHONPATH từ thư mục gốc project
export PYTHONPATH=$(pwd)          # Linux/macOS
$env:PYTHONPATH = (Get-Location).Path   # Windows PowerShell
```

**Lỗi `Connection refused` khi kết nối Qdrant**
```bash
# Kiểm tra container đang chạy
docker ps | grep qdrant

# Khởi động lại nếu cần
docker compose up -d qdrant
```

**Lỗi `No JSONL files matched` khi chạy script 02**
```bash
# Chạy script 01 trước để tạo data/processed/
python -m scripts.01_parse_chunk data/raw --config configs/default.yaml
```

**Lỗi `FileNotFoundError: bm25.pkl`**
```bash
# Chạy script 04 để tạo BM25 index
python -m scripts.04_search_sparse build --config configs/default.yaml
```

**Script 06/07 chạy chậm (không có GPU)**

Đây là bình thường — cross-encoder reranker chạy trên CPU tốn thời gian. Giảm `batch_size` trong `default.yaml` nếu bị out-of-memory, hoặc dùng model nhỏ hơn như `itdainb/PhoRanker`.
