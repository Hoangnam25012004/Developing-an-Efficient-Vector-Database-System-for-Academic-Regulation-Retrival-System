# 📚 reg-retrieval — Academic Regulation Retrieval System

End-to-end pipeline: parse → clean → chunk → embed → index → search (dense/sparse/hybrid) → rerank → evaluate → serve API → UI.

## 0) Chuẩn bị
```bash
python -m venv .venv && source .venv/bin/activate  # (Linux/Mac)
# Windows: .venv\Scripts\activate
pip install -U pip && pip install -r requirements.txt

# Khởi động Qdrant (vector DB)
docker compose up -d
