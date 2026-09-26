#!/usr/bin/env bash
# Container start: pinned models -> vector seed -> API (one process, see README).
set -euo pipefail
cd /app

python scripts/fetch_models.py
python scripts/seed_qdrant.py --if-missing

exec python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 1
