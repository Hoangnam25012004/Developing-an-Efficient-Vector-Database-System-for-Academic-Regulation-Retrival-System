#!/usr/bin/env bash
# Start FastAPI backend (serves both API + static UI at /).
# Usage:
#   ./start.sh           # local only
#   ./start.sh --tunnel  # expose via Cloudflare tunnel (Vast.ai / remote)
#
# Press Ctrl+C to stop.

cd "$(dirname "$0")"

# Load .env
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

TUNNEL=false
[ "$1" = "--tunnel" ] && TUNNEL=true

PIDS=()

cleanup() {
    echo ""
    echo "Stopping all services..."
    for pid in "${PIDS[@]}"; do
        kill "$pid" 2>/dev/null
    done
    wait "${PIDS[@]}" 2>/dev/null
    rm -f /tmp/cf_be.log
    echo "Done."
}
trap cleanup SIGINT SIGTERM

# ── Backend (also serves UI at /) ─────────────────────────────────────────────
echo "[BE] Starting FastAPI on :8000 ..."
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload &
PIDS+=($!)

# Wait for backend ready
echo "[BE] Waiting for backend..."
for i in $(seq 1 30); do
    curl -sf http://localhost:8000/health > /dev/null 2>&1 && break
    sleep 1
done
echo "[BE] Backend ready."

# ── Tunnel for Backend (optional) ─────────────────────────────────────────────
APP_URL="http://localhost:8000"

if [ "$TUNNEL" = true ]; then
    cloudflared tunnel --url http://localhost:8000 --no-autoupdate > /tmp/cf_be.log 2>&1 &
    PIDS+=($!)

    echo "[Tunnel] Waiting for tunnel URL..."
    for i in $(seq 1 20); do
        APP_URL=$(grep -o 'https://[a-zA-Z0-9.-]*\.trycloudflare\.com' /tmp/cf_be.log 2>/dev/null | head -1)
        [ -n "$APP_URL" ] && break
        sleep 1
    done

    if [ -z "$APP_URL" ]; then
        echo "[Tunnel] Warning: could not detect tunnel URL, using localhost."
        APP_URL="http://localhost:8000"
    fi
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "=========================================="
echo "  App (UI + API) → $APP_URL"
echo "  API Docs       → $APP_URL/docs"
echo "  Press Ctrl+C to stop."
echo "=========================================="

wait "${PIDS[@]}"
