#!/usr/bin/env bash
# Run all automated tests (backend pytest + frontend type-check, vitest and build).
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO/backend"
[ -x .venv/bin/python ] || { python3 -m venv .venv; .venv/bin/python -m pip install -q -r requirements-dev.txt; }
.venv/bin/python -m pytest -q
cd "$REPO/frontend"
[ -d node_modules ] || npm ci
npm run typecheck
npm test
npm run build
