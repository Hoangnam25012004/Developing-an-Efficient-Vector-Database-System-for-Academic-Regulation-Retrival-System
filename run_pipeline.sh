#!/usr/bin/env bash
# Run full indexing pipeline
set -e

echo "=== Step 1: Parse & chunk PDFs ==="
python3 -m src.pipeline.01_parse_chunk --config config.yaml

echo "=== Step 2: Embed & index to Qdrant ==="
python3 -m src.pipeline.02_embed_index --config config.yaml

echo "=== Step 3: Build BM25 index ==="
python3 -m src.pipeline.03_bm25_index --config config.yaml

echo ""
echo "Pipeline complete. Start the app with:"
echo "  ./start.sh                  # FastAPI (UI + API) on :8000"
