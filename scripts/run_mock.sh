#!/usr/bin/env bash
# One-command demo without hardware: clearly labeled SYNTHETIC data for every device.
# Usage: scripts/run_mock.sh            (auth token printed below)
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO/backend"
if [ ! -x .venv/bin/python ]; then
  echo "Creating dev virtualenv…"; python3 -m venv .venv
  .venv/bin/python -m pip install -q -r requirements-dev.txt
fi
if [ ! -f "$REPO/frontend/dist/index.html" ]; then
  echo "Dashboard not built yet: run 'cd frontend && npm ci && npm run build' (or use scripts/run_dev.sh)."
fi
export STRIX_MOCK=true
export STRIX_MOCK_SCENE="${STRIX_MOCK_SCENE:-cups}"   # shell-game demo of the object memory; "outdoor" for people/car
export STRIX_DATA_DIR="${STRIX_DATA_DIR:-$REPO/data/mock}"
export STRIX_AUTH_TOKEN="${STRIX_AUTH_TOKEN:-demo-$(python3 -c 'import secrets;print(secrets.token_hex(4))')}"
echo "STRIX-1 demo mode → http://localhost:${STRIX_PORT:-8000}   access token: ${STRIX_AUTH_TOKEN}"
exec .venv/bin/python -m app.main
