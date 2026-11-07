#!/usr/bin/env bash
# Run the STRIX-1 backend (serves the built dashboard on STRIX_PORT, default 8000).
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO/backend"
exec .venv/bin/python -m app.main "$@"
