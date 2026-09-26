# Hệ thống truy xuất văn bản pháp quy học thuật

Hệ thống tra cứu quy chế, quy định của Trường Đại học Quốc tế – ĐHQG-HCM: tìm kiếm lai (vector + BM25), rerank bằng cross-encoder và **trả lời bằng cách trích nguyên văn** Điều/Khoản kèm số trang. Hệ thống không dùng mô hình sinh (LLM), nên câu trả lời không chứa nội dung nằm ngoài tài liệu.

Đây là mã nguồn, bộ test collection và các script của bài báo *Developing an Efficient Vector Database System for Academic Regulation Retrieval System* (Dang Hoang Nam, KSE 2026). Xem [Tái lập kết quả paper](#tái-lập-kết-quả-paper).

---

## Mục Lục

- [Chạy Nhanh Bằng Docker](#chạy-nhanh-bằng-docker)
- [Tổng Quan](#tổng-quan)
- [Kiến Trúc Hệ Thống](#kiến-trúc-hệ-thống)
- [Luồng Chạy (Data Flow)](#luồng-chạy-data-flow)
- [Cấu Trúc Dự Án](#cấu-trúc-dự-án)
- [Công Nghệ Sử Dụng](#công-nghệ-sử-dụng)
- [Yêu Cầu Hệ Thống](#yêu-cầu-hệ-thống)
- [Cài Đặt](#cài-đặt)
- [Cấu Hình](#cấu-hình)
- [Chạy Pipeline Lập Chỉ Mục](#chạy-pipeline-lập-chỉ-mục)
- [Khởi Động Ứng Dụng](#khởi-động-ứng-dụng)
- [API Endpoints](#api-endpoints)
- [Giao Diện Web (Static SPA)](#giao-diện-web-static-spa)
- [Đánh Giá Hệ Thống](#đánh-giá-hệ-thống)
- [Kết Quả Đánh Giá](#kết-quả-đánh-giá)
- [Tùy Chỉnh & Mở Rộng](#tùy-chỉnh--mở-rộng)
- [Tái Lập Kết Quả Paper](#tái-lập-kết-quả-paper)
- [Vấn Đề Đã Biết](#vấn-đề-đã-biết)
- [Giấy Phép](#giấy-phép)
- [Trích Dẫn](#trích-dẫn)

---

## Chạy Nhanh Bằng Docker

Cần Docker (Docker Desktop trên Windows/macOS) được cấp **ít nhất 6 GB RAM** và khoảng **6 GB** đĩa trống.

```bash
git clone https://github.com/Hoangnam25012004/Developing-an-Efficient-Vector-Database-System-for-Academic-Regulation-Retrival-System.git arrs
cd arrs
docker compose up -d
```

Mở http://127.0.0.1:8000. Lần chạy đầu, container tải hai model (khoảng 2.8 GB, đúng revision đã dùng trong paper, ghi ở `models.lock.json`) và nạp 4.224 vector của corpus vào Qdrant từ `data/qdrant_seed/`, nên mất vài phút. Các lần sau khởi động trong khoảng 30 giây. Theo dõi tiến trình: `docker compose logs -f app`.

- Image dựng sẵn được lấy từ GitHub Container Registry. Muốn tự build từ mã nguồn: `docker compose up -d --build`.
- Đổi cổng: đặt `APP_PORT=8080` (biến môi trường hoặc file `.env` cạnh `docker-compose.yml`).
- Tài liệu upload thêm nằm trong volume `arrs_data`. `docker compose down -v` xoá toàn bộ volume và đưa hệ thống về corpus gốc.
- Chạy script đánh giá trong container: `docker compose exec app python eval/run_ablation.py --gt eval/test_queries_gt_v2.jsonl`.
- Mỗi câu trả lời mất khoảng 15–30 giây trên CPU; phần lớn thời gian là cross-encoder rerank (paper, mục V-B).

> **Bảo mật:** API không có đăng nhập. Ai truy cập được cổng của ứng dụng đều có thể upload/xoá tài liệu, chạy lại chỉ mục, sửa `/config` và chạy `/evaluate`. Vì vậy compose chỉ mở cổng trên `127.0.0.1`. Chỉ đặt `APP_BIND=0.0.0.0` (mở ra mạng LAN) trong mạng tin cậy, và đừng đưa cổng này ra Internet khi chưa có reverse proxy có xác thực.

---

## Tổng Quan

Hệ thống trả lời câu hỏi của sinh viên liên quan đến:
- Quy chế học vụ, kỷ luật sinh viên
- Luật và quyết định của nhà trường
- Thông báo, phụ lục quy định

**Điểm nổi bật:**
- **LLM-free synthesis**: Không gọi API LLM — câu trả lời được sinh bằng 4-tier template engine, trích nguyên văn các Khoản pháp lý → zero hallucination, 0$ chi phí, kết quả tái lập 100%
- **Hybrid Search**: Kết hợp Dense Retrieval (vector embedding) + Sparse Retrieval (BM25) để tối đa độ bao phủ
- **Reciprocal Rank Fusion (RRF)**: Hợp nhất kết quả từ hai nguồn tìm kiếm mà không cần học tham số
- **Cross-Encoder Reranking**: Mô hình reranker tiếng Việt (AITeamVN/Vietnamese_Reranker) sắp xếp lại kết quả chính xác hơn
- **Source Diversity**: Giới hạn tối đa 5 chunk từ cùng một tài liệu để tránh lặp lại
- **Deterministic Evaluation**: 7 retrieval metrics (P/R/F1/HitRate/MRR/MAP/NDCG @k) + 5 generation metrics (`answer_recall` bigram, BERTScore xlm-roberta + multilingual, ROUGE-1/L) — đánh giá bằng so khớp lexical/embedding với ground truth
- **Vietnamese-Aware Chunking**: Phân đoạn văn bản theo cấu trúc pháp lý (Chương / Điều / Khoản) với fallback paragraph cho thông báo/phụ lục

---

## Kiến Trúc Hệ Thống

```
┌─────────────────────────────────────────────────────────────────┐
│                        PIPELINE LẬP CHỈ MỤC                    │
│                        (Chạy một lần)                           │
├───────────────┬────────────────────────┬────────────────────────┤
│  01_parse_    │   02_embed_index.py    │  03_bm25_index.py      │
│  chunk.py     │                        │                        │
│               │                        │                        │
│  PDF → Chunk  │  Chunk → Embedding     │  Chunk → BM25 Index    │
│  (PyMuPDF +   │  (vietnamese-bi-       │  (rank-bm25,           │
│   Legal regex)│   encoder, 768-dim)    │   underthesea)         │
│               │  → Qdrant collection   │  → bm25.pkl            │
└───────────────┴────────────────────────┴────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                      LUỒNG XỬ LÝ CÂU HỎI                       │
│                      (Thời gian thực)                           │
│                                                                 │
│  User Query                                                     │
│      │                                                          │
│      ├──────────────────────────────────────────┐              │
│      │                                          │              │
│      ▼                                          ▼              │
│  ┌──────────────┐                    ┌──────────────────┐      │
│  │ Dense Search │                    │  Sparse Search   │      │
│  │  (Qdrant)    │                    │    (BM25)        │      │
│  │              │                    │                  │      │
│  │ Embed query  │                    │ Tokenize query   │      │
│  │ → Cosine     │                    │ → BM25 scoring   │      │
│  │   similarity │                    │                  │      │
│  │ → top_k=30   │                    │ → top_k=30       │      │
│  └──────┬───────┘                    └────────┬─────────┘      │
│         │                                     │                │
│         └──────────────┬────────────────────--┘                │
│                        │                                        │
│                        ▼                                        │
│              ┌──────────────────┐                              │
│              │  RRF Fusion      │                              │
│              │ (Reciprocal Rank │                              │
│              │  Fusion, k=60)   │                              │
│              │ → top_fusion=20  │                              │
│              └────────┬─────────┘                              │
│                       │                                         │
│                       ▼                                         │
│              ┌──────────────────┐                              │
│              │  Cross-Encoder   │                              │
│              │   Reranking      │                              │
│              │ (AITeamVN/       │                              │
│              │  Vietnamese_     │                              │
│              │  Reranker)       │                              │
│              │ + Dedup          │                              │
│              │ + Diversity (≤5  │                              │
│              │   per source)    │                              │
│              │ → top_k=10       │                              │
│              └────────┬─────────┘                              │
│                       │                                         │
│                       ▼                                         │
│              ┌──────────────────┐                              │
│              │ LLM-free 4-tier  │                              │
│              │   Synthesis      │                              │
│              │                  │                              │
│              │ Tier 1: 1 Khoản  │                              │
│              │   áp đảo         │                              │
│              │ Tier 2: nhiều    │                              │
│              │   Khoản cùng Điều│                              │
│              │ Tier 3: đa nguồn │                              │
│              │ Tier 4: not found│                              │
│              └────────┬─────────┘                              │
│                       │                                         │
│                       ▼                                         │
│              Answer (Markdown) + Sources + Scores               │
└─────────────────────────────────────────────────────────────────┘
```

---

## Luồng Chạy (Data Flow)

### 1. Indexing Pipeline (Offline — chạy một lần)

```
data/<category>/**/*.pdf
       │
       ▼
[01_parse_chunk.py]
  - Trích xuất văn bản bằng PyMuPDF (+ OCR fallback cho PDF scan)
  - Phát hiện cấu trúc pháp lý: Chương / Điều / Khoản / Điểm
  - Dual chunker:
      • StructuredChunker  → đơn vị Khoản (Luật / Quy chế / Quyết định)
      • ParagraphChunker   → đơn vị đoạn  (Thông báo / Phụ lục)
  - Table extraction: 1 chunk per data row
  - Output: data/processed/*.jsonl
    {chunk_id, source, page, text, doc_group, doc_type, doc_number,
     issuing_body, chapter, article, khoan, section_type, ...}
       │
       ├──────────────────────────────────┐
       │                                  │
       ▼                                  ▼
[02_embed_index.py]              [03_bm25_index.py]
  - Đọc tất cả JSONL files         - Tokenize text (underthesea)
  - Embed bằng vietnamese-          - Xây BM25Okapi index
    bi-encoder (768 chiều)            (k1=1.5, b=0.75)
  - Upsert vào Qdrant               - Lưu bm25.pkl
    collection "rag_docs"
```

### 2. Query Pipeline (Online — mỗi câu hỏi)

```
User Question
    │
    ├─── Dense: Embed → Tìm cosine similarity trong Qdrant → 30 kết quả
    │
    ├─── Sparse: Tokenize → BM25 scoring → 30 kết quả
    │
    ▼
RRF Fusion: Hợp nhất 2 danh sách → score(d) = Σ 1/(k + rank_i(d)) → 20 kết quả
    │
    ▼
Cross-Encoder Reranking:
    - Chấm điểm lại từng cặp (query, chunk) — truncate 512 chars
    - Loại trùng lặp (chunk_id + tiền tố văn bản)
    - Giới hạn 5 chunk mỗi nguồn tài liệu
    - Giữ lại top 10
    │
    ▼
LLM-free Tier Classifier (rag_chain.py):
    top_score = docs[0]._score_rerank
    gap       = docs[0]._score_rerank − docs[1]._score_rerank
    │
    ├─ top_score < 0.05                              → Tier 4: "không tìm thấy"
    ├─ gap ≥ 0.30                                    → Tier 1: 1 Khoản verbatim
    ├─ top 4 docs cùng source + cùng article         → Tier 2: nhóm theo Khoản
    └─ else                                          → Tier 3: multi-source
    │
    ▼
Response: {
  answer:  Markdown with "Tài liệu đính kèm" footer,
  sources: [{chunk_id, source, page, article, khoan, score_rrf, score_rerank}]
}
```

---

## Cấu Trúc Dự Án

```
chatbot_rag_ranking/
├── api/                          # FastAPI backend
│   ├── __init__.py
│   └── main.py                  # REST API server
│
├── src/                          # Core logic
│   ├── config.py                # Đọc config.yaml + thay thế ${ENV_VAR}
│   ├── chat/
│   │   └── rag_chain.py        # Orchestrator + 4-tier LLM-free synthesis
│   ├── pipeline/
│   │   ├── 01_parse_chunk.py   # Parse PDF → chunks JSONL (+ OCR fallback)
│   │   ├── 02_embed_index.py   # Embed + index vào Qdrant
│   │   └── 03_bm25_index.py    # Xây BM25 sparse index
│   ├── retrieval/
│   │   ├── vector_store.py     # Dense retrieval (Qdrant)
│   │   ├── bm25_retriever.py   # Sparse retrieval (BM25)
│   │   ├── hybrid_retriever.py # RRF fusion
│   │   └── reranker.py         # Cross-encoder + dedup + diversity
│   └── evaluation/
│       ├── evaluator.py        # Pipeline đánh giá đa luồng
│       └── metrics.py          # P/R/F1/HitRate/MRR/MAP/NDCG + BERTScore/ROUGE
│
├── ui/
│   └── index.html               # Static SPA (chat + dashboard + admin)
│
├── eval/
│   ├── run_eval.py              # Wrapper chạy evaluation
│   ├── annotate_relevant_ids.py # Gán nhãn ground-truth (semi-auto)
│   ├── reannotate_for_new_chunks.py
│   ├── compute_metrics_now.py / check_match.py / verify_gt.py / fix_groundtruth.py
│   ├── test_queries_gt.jsonl    # Bộ test có ground-truth
│   └── results/                 # Kết quả đánh giá (JSON)
│
├── data/
│   ├── Luat-quoc-gia/                              # Group 1 — Luật quốc gia
│   ├── Thong-tu-quy-che-cap-bo-gddt/               # Group 2 — TT Bộ GD&ĐT
│   ├── Quyet-dinh-quy-che-cap-dhqg-hcm/            # Group 3 — QĐ ĐHQG
│   ├── Quyet-dinh-quy-che-quy-dinh-day-du-cua-…/   # Group 4-5 — QĐ trường
│   ├── Phu-luc/                                    # Group 6 — Phụ lục
│   ├── Thong-bao/                                  # Group 7 — Thông báo
│   └── processed/               # Output của bước 01
│       ├── *.jsonl              # Chunks (1 file / PDF)
│       └── bm25.pkl             # BM25 index
│
├── config.yaml                  # Cấu hình toàn hệ thống
├── .env                         # QDRANT_URL, QDRANT_API_KEY (không commit)
├── requirements.txt             # Python dependencies
├── start.bat / start.sh         # Khởi động FastAPI (UI tĩnh tự serve)
└── run_pipeline.sh              # Chạy indexing pipeline (01 → 02 → 03)
```

---

## Công Nghệ Sử Dụng

| Thành phần | Công nghệ | Chi tiết |
|-----------|-----------|----------|
| **Synthesis** | LLM-free 4-tier (rag_chain.py) | Trích nguyên văn Khoản theo template — không gọi API |
| **Embeddings** | `bkai-foundation-models/vietnamese-bi-encoder` | 768 chiều, tiếng Việt, chạy local |
| **Vector DB** | Qdrant | Cosine similarity, cloud hoặc local |
| **Sparse Search** | rank-bm25 (BM25Okapi) | k1=1.5, b=0.75 |
| **Reranker** | `AITeamVN/Vietnamese_Reranker` | Cross-encoder tiếng Việt, local |
| **PDF Parsing** | PyMuPDF (+ pytesseract OCR fallback) | Trích xuất văn bản + số trang + table |
| **Tokenization** | underthesea | Tách từ tiếng Việt |
| **Backend** | FastAPI + Uvicorn | ASGI, async; serve UI tĩnh tại `/` |
| **Frontend** | HTML/CSS/JS thuần (Chart.js, marked) | SPA tĩnh, không build step |
| **Config** | PyYAML + python-dotenv | Env var substitution |

---

## Yêu Cầu Hệ Thống

- **Python**: 3.10 hoặc cao hơn (đã test trên 3.11)
- **RAM**: Tối thiểu 8 GB; khuyến nghị 16 GB
- **GPU**: Không bắt buộc. Trên CPU mỗi câu hỏi mất khoảng 15–30 giây, phần lớn là rerank; có CUDA thì embed và rerank nhanh hơn nhiều
- **Disk**: ~4 GB (model: bi-encoder ~0.5 GB, reranker ~2.3 GB; cùng dữ liệu và chỉ mục)
- **Qdrant**: Local (Docker) hoặc Qdrant Cloud
- **Không cần API key của LLM** — hệ thống chạy hoàn toàn local cho cả retrieval, rerank và synthesis. Chỉ cần `QDRANT_URL` + `QDRANT_API_KEY` nếu dùng Qdrant Cloud.
---

## Cài Đặt

### Bước 1: Clone dự án

```bash
git clone https://github.com/Hoangnam25012004/Developing-an-Efficient-Vector-Database-System-for-Academic-Regulation-Retrival-System.git arrs
cd arrs
```

### Bước 2: Tạo virtual environment

```bash
python -m venv venv
source venv/bin/activate        # Linux/Mac
# venv\Scripts\activate.bat     # Windows
```

### Bước 3: Cài dependencies

```bash
pip install -r requirements.txt
```

> **Lưu ý:** Lần đầu chạy sẽ tự tải model từ Hugging Face:
> - `bkai-foundation-models/vietnamese-bi-encoder` (~0.5 GB)
> - `AITeamVN/Vietnamese_Reranker` (~2.3 GB)
>
> Revision dùng trong paper được ghi ở `models.lock.json` (image Docker luôn dùng đúng các revision này). Phiên bản thư viện của môi trường đo nằm ở `requirements.lock`: cài `torch==2.11.0` từ `https://download.pytorch.org/whl/cpu` trước, rồi `pip install -r requirements.lock`.

### Bước 4: Cài đặt Qdrant (Local)

```bash
# Dùng Docker
docker run -p 6333:6333 qdrant/qdrant

# Hoặc Qdrant Cloud: https://cloud.qdrant.io/ (nên dùng cái này nhé)
```

### Bước 5: Tạo file `.env`

```dotenv
QDRANT_URL=http://localhost:6333         # hoặc URL Qdrant Cloud
QDRANT_API_KEY=                          # để trống nếu dùng local Docker
```

> Hệ thống chạy LLM-free nên **không cần** `OPENAI_API_KEY`, `GEMINI_API_KEY`, `COHERE_API_KEY`.

---

## Cấu Hình

File `config.yaml` chứa toàn bộ cấu hình hệ thống:

```yaml
# Đường dẫn dữ liệu
data:
  raw_dir: "data"
  processed_dir: "data/processed"

# Chunking (informational — chunker thật tự quyết theo cấu trúc pháp lý)
chunking:
  chunk_size: 512        # giới hạn nội bộ của StructuredChunker cho Khoản
  chunk_overlap: 0       # ParagraphChunker dùng 1-câu overlap

# Embedding (chạy local, không API)
embedding:
  provider: "local"
  local_model: "bkai-foundation-models/vietnamese-bi-encoder"
  batch_size: 32

# Vector Store
vector_store:
  provider: "qdrant"
  qdrant_url: "${QDRANT_URL}"     # lấy từ biến môi trường
  qdrant_api_key: "${QDRANT_API_KEY}"
  collection_name: "rag_docs"
  vector_size: 768

# BM25 Sparse Index
bm25:
  index_path: "data/processed/bm25.pkl"
  k1: 1.5
  b: 0.75

# Retrieval
retrieval:
  top_k_dense: 30        # số kết quả dense
  top_k_sparse: 30       # số kết quả sparse
  top_k_fusion: 20       # số kết quả sau RRF
  rrf_k: 60              # hằng số RRF

# Reranking (cross-encoder local)
reranking:
  enabled: true
  provider: "cross-encoder"
  cross_encoder_model: "AITeamVN/Vietnamese_Reranker"
  top_k: 10              # số chunk đưa vào tier classifier
  max_per_source: 5      # tối đa 5 chunk cùng nguồn (cho phép nhiều Khoản cùng 1 Điều)
  min_score: 0.0         # 0 = không lọc ở tầng rerank; gating thực tế nằm ở tier 4

# LLM-free synthesis
llm:
  provider: "hybrid"                # "hybrid" (4-tier) | "extractive" (raw chunks)
  min_score: 0.05                   # < ngưỡng này → Tier 4 "không tìm thấy"
  single_dominance_gap: 0.30        # ≥ ngưỡng này → Tier 1 (1 Khoản áp đảo)
  not_found_message: "Tôi không tìm thấy thông tin này trong tài liệu hiện có."

# Evaluation
evaluation:
  test_queries_path: "eval/test_queries_gt.jsonl"
  k_values: [1, 3, 5, 10]
  output_dir: "eval/results"
```

---

## Chạy Pipeline Lập Chỉ Mục

> **Chỉ cần chạy một lần** khi thêm tài liệu mới hoặc lần đầu cài đặt.

```bash
chmod +x run_pipeline.sh
./run_pipeline.sh
```

Script này chạy tuần tự 3 bước:

```bash
# Bước 1: Parse PDF → JSONL chunks
python src/pipeline/01_parse_chunk.py

# Bước 2: Embed chunks → Qdrant
python src/pipeline/02_embed_index.py

# Bước 3: Xây BM25 sparse index
python src/pipeline/03_bm25_index.py
```
```
python -m src.pipeline.01_parse_chunk --only-files Tai-lieu-moi.pdf
python -m src.pipeline.01_parse_chunk --only-files Tai-lieu-A.pdf Tai-lieu-B.pdf
python -m src.pipeline.03_bm25_index --only-files Tai-lieu-moi.jsonl
```

**Output:**
- `data/processed/*.jsonl` — 35 file JSONL chứa các chunks
- Qdrant collection `reg_chunks` — dense vectors
- `data/processed/bm25.pkl` — BM25 index (~2.3 MB)

---

## Khởi Động Ứng Dụng

### Chạy Local

```bash
# Windows
start.bat

# Linux / Mac
chmod +x start.sh
./start.sh
```

UI tĩnh (`ui/index.html`) được FastAPI serve trực tiếp tại `/`, **không có** service riêng:

| Service | URL |
|---------|-----|
| Web UI (chat + dashboard) | http://localhost:8000 |
| API Docs (Swagger) | http://localhost:8000/docs |

### Chạy thủ công

```bash
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```

---

## API Endpoints

### `POST /chat`

Gửi câu hỏi và nhận câu trả lời từ RAG pipeline đầy đủ.

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "question": "Sinh viên vi phạm quy định thi hộ lần đầu bị xử lý như thế nào?",
    "top_k": 4
  }'
```

**Response:**
```json
{
  "answer": "Theo quy định, sinh viên vi phạm thi hộ lần đầu sẽ bị kỷ luật đình chỉ học tập 01 năm...",
  "sources": [
    {
      "source": "3.-Phu-luc-1-30122022-Signed-2.pdf",
      "page": 2,
      "text": "Điều 5. Hình thức kỷ luật...",
      "rrf_score": 0.0312,
      "rerank_score": 4.87
    }
  ]
}
```

---

### `POST /retrieve`

Chỉ truy xuất tài liệu liên quan, không gọi LLM.

```bash
curl -X POST http://localhost:8000/retrieve \
  -H "Content-Type: application/json" \
  -d '{
    "query": "hoãn thi",
    "top_k": 5
  }'
```

**Response:**
```json
{
  "results": [
    {
      "chunk_id": "abc123...",
      "source": "quy-che-hoc-vu.pdf",
      "page": 7,
      "text": "...",
      "rrf_score": 0.029,
      "rerank_score": 3.21
    }
  ]
}
```

---

### `POST /evaluate`

Chạy bộ đánh giá tự động trên `eval/test_queries_gt.jsonl` (đường dẫn từ `config.yaml`).

```bash
curl -X POST http://localhost:8000/evaluate
```

---

### `GET /health`

Kiểm tra trạng thái server. Luôn trả HTTP 200 khi API đang chạy.

```bash
curl http://localhost:8000/health
# {"status": "ok", "ready": true, "qdrant": true, "timestamp": 1790412345.6}
```

- `ready`: model đã nạp xong.
- `qdrant`: Qdrant trả lời và có collection cấu hình trong `config.yaml`. Thanh trạng thái của giao diện đọc field này ("System online" hoặc "Qdrant offline"). Kết quả được cache 5 giây.

---

### Tài liệu & index (upload v2)

| Endpoint | Mô tả |
|---|---|
| `POST /uploads` | Tải file lên vùng tạm và kiểm tra (multipart `files`, tuỳ chọn `group`) → báo cáo từng file |
| `POST /uploads/{id}/commit` | Xác nhận: chọn nhóm, hành động khi trùng tên (`add`/`replace`/`rename`/`skip`), ngôn ngữ, metadata → tạo job index |
| `DELETE /uploads/{id}` | Huỷ phiên tải lên |
| `GET /documents` | Mọi tài liệu kèm trạng thái index |
| `GET /documents/{tên}/file` | File gốc (PDF mở trên trình duyệt, DOCX tải về) |
| `POST /documents/{tên}/retry` | Index lại một tài liệu |
| `DELETE /documents/{tên}` · `DELETE /groups/{nhóm}` | Xoá file và xoá khỏi mọi chỉ mục |
| `GET /jobs/{id}` · `GET /jobs?active=1` | Tiến độ job index |
| `POST /reindex` | Đồng bộ toàn bộ (`{"mode": "sync"}` mặc định, Chat vẫn chạy; `"rebuild"` tạo lại collection) |
| `GET /index/health` | So số chunk theo tài liệu giữa JSONL, Qdrant và BM25 |
| `POST /upload-docs` | Endpoint cũ, giữ để tương thích (không còn ghi đè im lặng) |

---

## Giao Diện Web (Static SPA)

Truy cập `http://localhost:8000` để mở UI. UI là HTML/CSS/JS tĩnh, được FastAPI serve từ `ui/index.html`. Các view chính:

### 1. Chat

- Gõ câu hỏi bằng tiếng Việt
- Nhận câu trả lời Markdown kèm nguồn tài liệu (clickable PDF)
- Hiển thị metadata Điều / Khoản / số trang cho mỗi nguồn

### 2. Documents

- Liệt kê mọi tài liệu theo nhóm, kèm ngôn ngữ và **trạng thái index** (đã index / đang xử lý / lỗi / chưa index)
- Upload PDF/DOCX hai bước: *Kiểm tra* (báo cáo từng file) → *Tải lên & index* (index tăng dần ở chế độ nền)
- Xoá tài liệu/nhóm (tự xoá khỏi chỉ mục), *Index lại* tài liệu lỗi, *Đồng bộ chỉ mục* toàn bộ

### 3. Dashboard / Evaluation

- Xem stats corpus (tổng số chunk, số file theo nhóm)
- Xem kết quả evaluation gần nhất (Precision / Recall / NDCG theo k + generation metrics)
- Chỉnh sửa `config.yaml` ngay trên UI và reload chain

---

## Đánh Giá Hệ Thống

Hệ thống dùng **reference-based evaluation** — so sánh output của RAG pipeline với bộ ground truth do người gán nhãn, bằng các phép đo lexical (bigram, ROUGE) và embedding (BERTScore). Toàn bộ pipeline (cả retrieval, generation lẫn evaluation) đều không gọi LLM, nên kết quả tái lập 100% giữa các lần chạy và không phát sinh chi phí API.

### Bộ ground truth

> Bộ ground truth hợp lệ hiện nay là `eval/test_queries_gt_v2.jsonl` (100 câu, gán nhãn theo giao thức ở `eval/ANNOTATION.md`, dùng trong paper). Các file v1 và strict chỉ được giữ lại để đối chiếu lịch sử.

File: `eval/test_queries_gt.jsonl` (đường dẫn đặt trong `config.yaml > evaluation.test_queries_path`).

Mỗi dòng là 1 record JSON:

```json
{
  "id": 1,
  "question": "Quy định MGHP áp dụng cho đối tượng sinh viên nào?",
  "answer": "Áp dụng cho tất cả sinh viên đại học chính quy văn bằng thứ nhất...",
  "relevant_ids": ["2c6b7ecb31d226d2", "66993bab8e486d74"],
  "sources": [{ "source": "9.-QD688-MGHP-2024.pdf", "page": 8, "..." }]
}
```

- `answer` (reference) — bắt buộc cho generation metrics
- `relevant_ids` (ground-truth chunk_ids) — bắt buộc cho retrieval metrics; query nào không có sẽ bị skip ở phần retrieval

### Gán nhãn & cập nhật ground truth

Bộ test `eval/test_queries_gt.jsonl` đã có sẵn. Khi cần re-annotate (ví dụ sau khi đổi chiến lược chunking):

```bash
# Gán relevant_ids cho từng câu (semi-automatic, không cần LLM)
python eval/annotate_relevant_ids.py

# Re-annotate khi đổi chunking → chunk_id thay đổi
python eval/reannotate_for_new_chunks.py
```

### Chạy đánh giá

```bash
# Mặc định: dùng test_queries_path từ config + 8 RAG workers song song
python -m src.evaluation.evaluator

# Tuỳ chọn override
python -m src.evaluation.evaluator --config config.yaml --workers 8 --gt eval/test_queries_gt.jsonl
```

Output (mỗi lần chạy):
- `eval/results/summary_<timestamp>.json` — metrics tổng hợp
- `eval/results/details_<timestamp>.json` — per-query (gồm answer, retrieved_ids, score từng metric)

### Pipeline đánh giá (3 phase)

```
Ground Truth JSONL
       │
       ▼
[Phase 1] RAG pipeline song song (ThreadPoolExecutor, 8 workers)
       │   chain.query(question) → answer + 10 chunks
       ▼
[Phase 2] Retrieval metrics      ← chỉ query có relevant_ids
       │   so retrieved_ids vs relevant_ids cho mỗi k ∈ {1,3,5,10}
       ▼
[Phase 3] Generation metrics     ← tất cả query
           so answer vs ref_answer (bigram + BERTScore + ROUGE)
```

### Các metric chi tiết

**Retrieval (7 metrics × 4 k = 28 con số)** — implement trong [src/evaluation/metrics.py](src/evaluation/metrics.py):

| Metric | Ý nghĩa |
|---|---|
| `precision@k` | % chunk trong top-k là đúng |
| `recall@k` | % chunk đúng tìm được trong top-k |
| `f1@k` | Trung bình harmonic của P và R |
| `hit_rate@k` | Có ít nhất 1 chunk đúng trong top-k? (0/1) |
| `mrr@k` | 1/rank của chunk đúng đầu tiên |
| `map@k` | Average precision trên top-k |
| `ndcg@k` | DCG/IDCG — thưởng cho chunk đúng đứng cao |

**Generation (5 metrics)** — không cần LLM:

| Metric | Mô hình / công thức | Vai trò |
|---|---|---|
| **`answer_recall`** | Bigram recall: `|ref∩hyp| / |ref|` | **Primary** — không phạt verbose answer (phù hợp extractive RAG) |
| **`bertscore_xlmr`** | `xlm-roberta-base` F1 | **Primary** — semantic similarity tốt cho tiếng Việt |
| `rouge1`, `rougeL` | `rouge_score` package | Legacy, để so sánh |
| `bertscore_multi` | `bert-base-multilingual-cased` F1 | Legacy, kém hơn xlm-roberta cho tiếng Việt (0.61 vs 0.79) |

> **Lưu ý:** không dùng "Faithfulness / Answer Relevance / Context Precision" kiểu RAGAS vì những metric đó cần LLM-judge và kết quả không tái lập giữa các lần chạy.

### Thử nghiệm hyperparameter

Sửa các giá trị `top_k_dense`, `top_k_sparse`, `top_k_fusion`, `reranking.top_k`, `reranking.max_per_source` trong `config.yaml`, rồi chạy lại `python -m src.evaluation.evaluator`. So sánh giữa các `summary_*.json` trong `eval/results/`.

---

## Kết Quả Đánh Giá

> **Lưu ý:** Các bảng trong mục này là kết quả nội bộ ngày 2026-05-01 trên bộ ground truth cũ (v1). Bộ này sau đó được chứng minh là gán nhãn theo văn bản chứ không theo Khoản (paper, mục IV-B), nên các con số dưới đây không dùng để so sánh. Số liệu chính thức: [Tái lập kết quả paper](#tái-lập-kết-quả-paper).

> Bộ test: **30 câu hỏi** đã gán `relevant_ids`, đánh giá ngày 2026-05-01 với cấu hình mặc định (`config.yaml`: hybrid retrieval `top_k_dense=top_k_sparse=30`, RRF `top_k_fusion=20`, reranker `top_k=10`, `max_per_source=5`, generation = LLM-free Tier).

### Retrieval Metrics

| Metric | k=1 | k=3 | k=5 | k=10 |
|---|---|---|---|---|
| Precision@k | **0.767** | 0.467 | 0.360 | 0.233 |
| Recall@k    | 0.039 | 0.071 | 0.092 | 0.118 |
| F1@k        | 0.073 | 0.122 | 0.143 | 0.152 |
| Hit Rate@k  | **0.767** | 0.800 | 0.867 | **0.867** |
| MRR@k       | 0.767 | 0.783 | 0.797 | **0.797** |
| MAP@k       | 0.767 | 0.450 | 0.310 | 0.179 |
| NDCG@k      | 0.767 | 0.533 | 0.440 | 0.323 |

### Generation Metrics

| Metric | Score | Loại |
|---|---|---|
| `answer_recall`     | 0.357 | Primary |
| `bertscore_xlmr`    | **0.794** | Primary |
| `rouge1`            | 0.189 | Legacy |
| `rougeL`            | 0.141 | Legacy |
| `bertscore_multi`   | 0.614 | Legacy |

### Nhận xét

- **Precision@1 = 76.7%** — chunk top-1 sau rerank gần như luôn đúng. Đây là điểm mạnh lớn nhất, đến từ cross-encoder rerank trên ứng viên RRF chất lượng cao.
- **Hit Rate@10 = 86.7%** — có 13% câu hỏi mà không chunk đúng nào lọt vào top-10. Cần soi `details_*.json` để xác định nguyên nhân (chunking miss, embedding miss, hay câu hỏi quá mơ hồ).
- **Recall@k thấp (4–12%)** vì 1 câu hỏi thường có **nhiều `relevant_ids`** (6–15 Khoản liên quan), trong khi system chỉ trả về tối đa 10 chunk → mathematical ceiling.
- **`bertscore_xlmr = 0.79`** cho thấy answer bám sát semantic của reference; **`answer_recall = 0.36`** thấp hơn vì system trích nguyên văn (xuất hiện cùng bigram với ref khi và chỉ khi trùng đoạn pháp luật).
- **ROUGE-1/L thấp (0.14–0.19)** — đúng như dự đoán: extractive system trả lời rất verbose (Tier 3 có thể đến 10 đoạn) → ROUGE F1 bị penalty độ dài. Đây là lý do `answer_recall` được chọn làm primary metric.

---

## Tùy Chỉnh & Mở Rộng

### Thêm tài liệu mới

**Cách khuyến nghị — qua giao diện** (màn hình Documents → *📥 Update Documents*):
kéo thả file PDF/DOCX, chọn nhóm, bấm *Kiểm tra*. Hệ thống đọc thử từng file
(loại file thật, mật khẩu, trùng tên/trùng nội dung, gợi ý ngôn ngữ Việt/Anh),
bạn xác nhận rồi file được index ở chế độ nền — chỉ tài liệu mới được xử lý,
Chat vẫn hoạt động, thường xong sau vài chục giây. Cột *Trạng thái* cho biết
file nào đã tìm được trong Chat, đang xử lý hay bị lỗi (kèm lý do, nút *Index lại*).
Xoá tài liệu/nhóm cũng tự xoá khỏi chỉ mục. Thiết kế chi tiết:
[`docs/design/upload-documents-v2.md`](docs/design/upload-documents-v2.md).

**Cách thủ công** (tái lập đúng pipeline offline của paper):
1. Đặt file PDF/DOCX vào `data/<nhóm>/`
2. Chạy lại indexing pipeline:
   ```bash
   ./run_pipeline.sh
   ```
   `01_parse_chunk` có thêm cờ `--prune` để xoá các file `data/processed/*.jsonl`
   không còn tài liệu gốc (mặc định tắt, giữ nguyên hành vi cũ).

> Server phải chạy **một process** (như `start.bat`): hàng đợi index và khoá
> commit nằm trong process API. Không chạy uvicorn với `--workers > 1`.

### Chạy test

```bash
venv\Scripts\python -m unittest discover -s tests -t .
```

Test end-to-end (cần Qdrant đang chạy; dùng bản sao dữ liệu và collection tạm,
không đụng `reg_chunks`): đặt `ARRS_E2E=1` rồi chạy `python -m unittest tests.test_api_e2e -v`.

### Tinh chỉnh ngưỡng synthesis

Trong `config.yaml`, mục `llm:`:
```yaml
llm:
  min_score: 0.10              # tăng → ít trả lời sai, tăng "không tìm thấy"
  single_dominance_gap: 0.20   # giảm → dễ rơi vào Tier 1 (1 Khoản verbatim)
```

### Đổi mode synthesis

```yaml
llm:
  provider: "extractive"   # in raw chunks thay vì format 4-tier (dùng để debug)
```

### Quick start trên Windows

```cmd
cd C:\chatbot_rag_ranking
start.bat
```

### Điều chỉnh số chunk truyền vào synthesis

```yaml
reranking:
  top_k: 6           # giảm context (mỗi chunk vẫn là 1 Khoản đầy đủ)
  max_per_source: 2  # giảm để tăng diversity giữa các nguồn
```

---

## Tái Lập Kết Quả Paper

Tag `kse2026-paper` là mã nguồn đúng như lúc đo các số liệu trong bài báo; nhánh `main` có thêm các thay đổi sau đó (ghi ở `docs/design/`).

- **Môi trường đo:** Windows 11, chỉ dùng CPU, Qdrant 1.16.1 chạy bằng Docker. Phiên bản thư viện ở `requirements.lock`, revision model ở `models.lock.json`.
- **Corpus:** 34 văn bản, 4.224 chunk trong `data/processed/`. Vector đúng như lúc đo nằm ở `data/qdrant_seed/`; nạp vào Qdrant bằng `python scripts/seed_qdrant.py` (image Docker tự làm việc này).

| Kết quả | Lệnh | Thời gian (CPU) |
|---|---|---|
| Bảng II và kiểm định McNemar (mục V-B) | `python eval/run_ablation.py --gt eval/test_queries_gt_v2.jsonl` | ~35–50 phút, chủ yếu là rerank |
| Mục V-F (ghép Khoản) | `python eval/compare_answers.py --n 20` | ~8 phút |
| Bảng III, Hình 2–3 (benchmark vector DB) | xem [`eval/README.md`](eval/README.md) (`eval/bench_vectordb.py`) | |

Các chỉ số chất lượng tái lập đến 4 chữ số; độ trễ thay đổi theo máy và tải (paper, mục IV-A). `eval/README.md` được viết trước bộ GT v2: khi chạy ablation, luôn truyền `--gt eval/test_queries_gt_v2.jsonl`.

---

## Vấn Đề Đã Biết

- **Chạy lại chỉ mục sẽ OCR lại các PDF scan.** Nút *Đồng bộ chỉ mục* (`POST /reindex`) parse lại mọi tài liệu. Trong container, OCR dùng Tesseract 5.3 của Debian, khác bản Tesseract 5.4 đã dùng để dựng chỉ mục đi kèm. Đo thực tế: 15 PDF có lớp chữ cho kết quả giống hệt, còn 19 PDF scan cho chunk khác đi. Muốn giữ đúng corpus của paper thì không cần chạy lại chỉ mục; `docker compose down -v` đưa hệ thống về trạng thái gốc.
- **Khử trùng trước rerank** (`src/retrieval/reranker.py`, `_dedup`) coi hai chunk là một khi 80 ký tự đầu giống nhau. 208/4.224 chunk bị ảnh hưởng, chủ yếu là các dòng của cùng một bảng. Bước này được giữ nguyên vì nó thuộc hệ thống đã được đo trong paper.
- **Dữ liệu đánh giá cũ:** `eval/test_queries_gt_100_v1.jsonl`, `eval/test_queries_gt_100_strict.jsonl` và `eval/cache/corpus_*` mang `chunk_id` từ trước khi chia lại chunk. Chỉ dùng `eval/test_queries_gt_v2.jsonl`.
- **Chia chunk:** đoạn "Điều N" rất ngắn có thể bị gộp vào chunk sau, hoặc bị bỏ nếu nằm ở cuối tài liệu (giữ nguyên như trong paper).
- `eval/make_tables.py` mặc định ghi vào `Report/tables.tex`, thư mục không có trong repo; truyền `--out` để chọn nơi ghi.

---

## Giấy Phép

- Mã nguồn: MIT ([`LICENSE`](LICENSE)).
- Bộ test collection trong `eval/` (GT v2, judgments, pool): CC BY 4.0 ([`eval/LICENSE`](eval/LICENSE)).
- Các PDF trong `data/` là văn bản của cơ quan ban hành, được đưa vào để tái lập nghiên cứu; chúng không thuộc phạm vi hai license trên.
- Model `bkai-foundation-models/vietnamese-bi-encoder` và `AITeamVN/Vietnamese_Reranker` (Apache-2.0) được tải từ Hugging Face khi chạy.

---

## Trích Dẫn

```bibtex
@inproceedings{dang2026regulation,
  author    = {Dang, Hoang Nam},
  title     = {Developing an Efficient Vector Database System for Academic Regulation Retrieval System},
  booktitle = {Proceedings of the International Conference on Knowledge and Systems Engineering (KSE)},
  year      = {2026}
}
```

Metadata trích dẫn cũng có trong [`CITATION.cff`](CITATION.cff).
