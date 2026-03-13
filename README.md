# 📚 reg-retrieval — Academic Regulation Retrieval System

End-to-end pipeline: parse → clean → chunk → embed → index → search (dense/sparse/hybrid) → rerank → evaluate → serve API → UI.

## 0) Chuẩn bị
```bash
python -m venv .venv && source .venv/bin/activate  # (Linux/Mac)
# Windows: .venv\Scripts\activate
pip install -U pip && pip install -r requirements.txt

# Khởi động Qdrant
cd C:\AcademicRegulation
docker compose up -d qdrant
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"   
#1 chạy API
cd C:\AcademicRegulation
.\.venv\Scripts\Activate.ps1
$env:PYTHONPATH = (Get-Location).Path
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000

#2 chạy UI
cd C:\AcademicRegulation
.\.venv\Scripts\Activate.ps1
$env:PYTHONPATH = (Get-Location).Path
streamlit run ui/app.py


### Pipeline
#A
cd C:\AcademicRegulation
.\.venv\Scripts\Activate.ps1

python -m scripts.01_parse_chunk data\raw --config configs\default.yaml --title "Quy chế" --version "2024"

docker run -p 6333:6333 qdrant/qdrant

python -m scripts.02_embed_ingest --config configs\default.yaml

python -m scripts.03_search_dense --config configs\default.yaml --query "buộc thôi học vì vi phạm gì" --top_k 10

python -m scripts.04_search_sparse build --config configs/default.yaml

python -m scripts.05_hybrid_rrf --config configs\default.yaml --query "buộc thôi học vì vi phạm gì" --top_k 10 --rrf_k 60

python -m scripts.06_rerank --config configs\default.yaml --query "buộc thôi học vì vi phạm gì" --top_k 30 --final_k 10

python -m scripts/07_eval_metrics.py --config configs/default.yaml --queries data/eval/queries.jsonl --qrels data/eval/qrels.jsonl --method rerank --top_k 50 --final_k 50 --report_k 10



#B
cd C:\AcademicRegulation
.\.venv\Scripts\Activate.ps1
docker run -p 6333:6333 qdrant/qdrant

cd C:\AcademicRegulation
.\.venv\Scripts\Activate.ps1
python serve.py

cd C:\AcademicRegulation
.\.venv\Scripts\Activate.ps1
streamlit run ui/app.py

python -m scripts.09_ocr_metrics --config configs/default.yaml

python -m scripts.10_make_qa_pools --config configs/default.yaml --questions data/eval/qa_questions.jsonl --out data/eval/qa_pools.jsonl --dense_k 50 --sparse_k 50 --rrf_k 60 --rerank_pool 100 --rerank_k 50 --max_candidates 200

# Khi parsing lại từ đầu
Remove-Item -Recurse -Force data\processed
New-Item -ItemType Directory -Path data\processed | Out-Null
python -m scripts.01_parse_chunk data/raw --config configs/default.yaml


# 07 
python -m scripts.07_eval_metrics --queries data/eval/queries.jsonl --qrels data/eval/qrels.jsonl --runs data/eval/runs_dense.jsonl --k 10
python -m scripts.07_eval_metrics --queries data/eval/queries.jsonl --qrels data/eval/qrels.jsonl --runs data/eval/runs_sparse.jsonl --k 10
python -m scripts.07_eval_metrics --queries data/eval/queries.jsonl --qrels data/eval/qrels.jsonl --runs data/eval/runs_hybrid.jsonl --k 10
python -m scripts.07_eval_metrics --queries data/eval/queries.jsonl --qrels data/eval/qrels.jsonl --runs data/eval/runs_hybrid_rerank.jsonl --k 10
python -m scripts.07_eval_metrics --queries data/eval/queries.jsonl --qrels data/eval/qrels.jsonl --runs data/eval/runs.jsonl --k 10 # runs.jsonl




python scripts/07_eval_metrics.py --queries data/eval/queries.jsonl --qrels data/eval/qrels.jsonl --runs data/eval/runs_bge_m3.jsonl --model "BAAI/bge-reranker-v2-m3" --output data/eval/comparison.csv

python scripts/07_eval_metrics.py --queries data/eval/queries.jsonl --qrels data/eval/qrels.jsonl --runs data/eval/runs_bge_m3.jsonl --model "BAAI/bge-reranker-v2-m3" --output data/eval/comparison.csv


# Check model 
# ── Model 1: BAAI/bge-reranker-v2-m3 ─────────────────────────────
# (already set in default.yaml)
python scripts/06_rerank.py --queries data/eval/queries.jsonl --output data/eval/runs_bge_m3.jsonl
python scripts/07_eval_metrics.py --queries data/eval/queries.jsonl --qrels data/eval/qrels.jsonl --runs data/eval/runs_bge_m3.jsonl --model "BAAI/bge-reranker-v2-m3" --output data/eval/comparison.csv

# ── Model 2: jinaai/jina-reranker-v2-base-multilingual ───────────
# Edit default.yaml → model_name: "jinaai/jina-reranker-v2-base-multilingual"
python scripts/06_rerank.py --queries data/eval/queries.jsonl --output data/eval/runs_jina.jsonl
python scripts/07_eval_metrics.py --queries data/eval/queries.jsonl --qrels data/eval/qrels.jsonl --runs data/eval/runs_jina.jsonl --model "jinaai/jina-reranker-v2-base-multilingual" --output data/eval/comparison.csv

# ── Model 3: itdainb/PhoRanker ────────────────────────────────────
# Edit default.yaml → model_name: "itdainb/PhoRanker"
python scripts/06_rerank.py --queries data/eval/queries.jsonl --output data/eval/runs_phoranker.jsonl
python scripts/07_eval_metrics.py --queries data/eval/queries.jsonl --qrels data/eval/qrels.jsonl --runs data/eval/runs_phoranker.jsonl --model "itdainb/PhoRanker" --output data/eval/comparison.csv

# ── Model 4: BAAI/bge-reranker-v2-gemma ──────────────────────────
# pip install FlagEmbedding   (first time only)
# Edit default.yaml → model_name: "BAAI/bge-reranker-v2-gemma"  +  use_fp16: true
python scripts/06_rerank.py --queries data/eval/queries.jsonl --output data/eval/runs_bge_gemma.jsonl
python scripts/07_eval_metrics.py --queries data/eval/queries.jsonl --qrels data/eval/qrels.jsonl --runs data/eval/runs_bge_gemma.jsonl --model "BAAI/bge-reranker-v2-gemma" --output data/eval/comparison.csv

# ── Model 5: BAAI/bge-reranker-v2-minicpm-layerwise ──────────────
# Edit default.yaml → model_name: "BAAI/bge-reranker-v2-minicpm-layerwise"  +  use_fp16: true
python scripts/06_rerank.py --queries data/eval/queries.jsonl --output data/eval/runs_bge_layerwise.jsonl
python scripts/07_eval_metrics.py --queries data/eval/queries.jsonl --qrels data/eval/qrels.jsonl --runs data/eval/runs_bge_layerwise.jsonl --model "BAAI/bge-reranker-v2-minicpm-layerwise" --output data/eval/comparison.csv


py scripts/07_eval_metrics.py --queries data/eval/queries.jsonl --qrels data/eval/qrels.jsonl --runs data/eval/runs_bge_m3.jsonl --model bge_m3 2>&1


python scripts/06_rerank.py --config configs/default.yaml --query "buộc thôi học vì vi phạm gì" --top_k 30 --final_k 10
