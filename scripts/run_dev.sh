#!/usr/bin/env bash
# Development: backend (mock by default) on :8000 + Vite dev server with hot reload on :5173.
# Usage: scripts/run_dev.sh [--hardware]
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MOCK=true; [ "${1:-}" = "--hardware" ] && MOCK=false
cd "$REPO/backend"
[ -x .venv/bin/python ] || { python3 -m venv .venv; .venv/bin/python -m pip install -q -r requirements-dev.txt; }
export STRIX_MOCK=$MOCK STRIX_AUTH_TOKEN="${STRIX_AUTH_TOKEN:-dev-token}"
[ "$MOCK" = true ] && export STRIX_DATA_DIR="${STRIX_DATA_DIR:-$REPO/data/mock}"
.venv/bin/python -m app.main &
BACK=$!
trap 'kill $BACK 2>/dev/null' EXIT
cd "$REPO/frontend"
[ -d node_modules ] || npm ci
echo "Dashboard (dev): http://localhost:5173   token: $STRIX_AUTH_TOKEN"
npm run dev
