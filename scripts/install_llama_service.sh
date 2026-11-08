#!/usr/bin/env bash
# Install llama-server as its own systemd service (recommended over QWEN_MANAGE_SERVER for production).
# Usage: scripts/install_llama_service.sh /path/to/model.gguf [threads=3] [ctx=4096] [llama-server path] [mmproj.gguf]
# The optional mmproj file is the vision projector of a Qwen-VL model (enables camera images).
# The unit binds to 127.0.0.1:8080 only; the STRIX-1 backend proxies all access.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODEL="${1:?model path required}"; THREADS="${2:-3}"; CTX="${3:-4096}"; BIN="${4:-$HOME/llama.cpp/build/bin/llama-server}"
MMPROJ="${5:-}"
EXTRA=""
if [ -n "$MMPROJ" ]; then
  [ -f "$MMPROJ" ] || { echo "mmproj not found: $MMPROJ"; exit 1; }
  EXTRA=" --mmproj $MMPROJ"
fi
[ -f "$MODEL" ] || { echo "model not found: $MODEL"; exit 1; }
[ -x "$BIN" ] || { echo "llama-server not found: $BIN (run scripts/build_llama_cpp.sh)"; exit 1; }
USER_NAME="$(id -un)"
sed -e "s|__USER__|$USER_NAME|g" -e "s|__BIN__|$BIN|g" -e "s|__MODEL__|$MODEL|g" -e "s|__THREADS__|$THREADS|g" -e "s|__CTX__|$CTX|g" -e "s|__EXTRA__|$EXTRA|g" \
  "$REPO/config/systemd/strix1-llama.service" | sudo tee /etc/systemd/system/strix1-llama.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable strix1-llama.service
sudo systemctl restart strix1-llama.service
echo "Installed. Logs: journalctl -u strix1-llama -f    Health: curl -s http://127.0.0.1:8080/health"
