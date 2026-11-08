#!/usr/bin/env bash
# One command to install STRIX-1's AI on the Raspberry Pi 5:
#   1. YOLO object detector: YOLOX-Tiny (fast, checksum-verified), and optionally the
#      600-object "everyday" model that also knows pens                -> models/
#   2. llama.cpp's llama-server, built natively                          -> ~/llama.cpp
#   3. Qwen vision-language model + its vision projector (GGUF)          -> models/
#   4. strix1-llama systemd service (listens on 127.0.0.1:8080 only; starts on every boot)
#   5. QWEN_* settings in .env, STRIX-1 restarted
#
# Usage:  scripts/setup_ai.sh             Qwen2-VL-2B-Instruct  (default: fast, keeps the Pi cooler)
#         scripts/setup_ai.sh --best      Qwen2.5-VL-3B-Instruct (better answers, slower and hotter)
#         add --everyday to also install the pens/phones/mugs detector without being asked
#
# Downloads about 2-3 GB; building llama.cpp takes 10-20 minutes. Run it inside tmux.
# Safe to re-run: finished steps are skipped. Asks for your sudo password (apt, systemd).
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
HF_REPO="ggml-org/Qwen2-VL-2B-Instruct-GGUF"
EVERYDAY=ask
for a in "$@"; do
  case "$a" in
    --best) HF_REPO="ggml-org/Qwen2.5-VL-3B-Instruct-GGUF" ;;
    --fast) HF_REPO="ggml-org/Qwen2-VL-2B-Instruct-GGUF" ;;
    --everyday) EVERYDAY=yes ;;
    --no-everyday) EVERYDAY=no ;;
    *) echo "unknown option: $a"; exit 2 ;;
  esac
done
LLAMA_DIR="$HOME/llama.cpp"
BIN="$LLAMA_DIR/build/bin/llama-server"
say() { printf '\n\033[32m==> %s\033[0m\n' "$*"; }

say "1/5  Object detector (YOLO)"
if [ -f models/yolox_tiny.onnx ]; then echo "already installed: models/yolox_tiny.onnx"
else scripts/get_detector_model.sh; fi
if [ ! -f models/yolov8n-oiv7.onnx ]; then
  if [ "$EVERYDAY" = ask ] && [ -t 0 ]; then
    read -r -p "Also install the everyday-objects detector (600 objects incl. pens; ~10 min, temporary 2 GB)? [y/N] " r
    [[ "$r" =~ ^[Yy]$ ]] && EVERYDAY=yes
  fi
  if [ "$EVERYDAY" = yes ]; then
    scripts/get_detector_model.sh --everyday || echo "WARNING: everyday model export failed; YOLOX-Tiny (80 objects) stays in use."
  fi
fi
if [ -f models/yolox_s.onnx ]; then echo "note: models/yolox_s.onnx is no longer used by default (heavier); delete it to save space."; fi

say "2/5  llama.cpp (llama-server)"
if [ -x "$BIN" ]; then echo "already built: $BIN"
else scripts/build_llama_cpp.sh --yes "$LLAMA_DIR"; fi

say "3/5  Qwen vision model: $HF_REPO"
mkdir -p models
# Ask Hugging Face which files exist instead of guessing names.
FILES="$(curl -fsSL "https://huggingface.co/api/models/$HF_REPO" | python3 -c '
import json, sys
d = json.load(sys.stdin)
files = [s["rfilename"] for s in d.get("siblings", []) if s["rfilename"].endswith(".gguf")]
mm = [f for f in files if "mmproj" in f.lower()]
mo = [f for f in files if "mmproj" not in f.lower()]
def pick(lst, prefs):
    for p in prefs:
        for f in lst:
            if p in f.lower():
                return f
    return lst[0] if lst else ""
print(pick(mo, ["q4_k_m", "q4_0", "q8_0"]))
print(pick(mm, ["q8_0", "f16", "bf16"]))
')"
MODEL_FILE="$(echo "$FILES" | sed -n 1p)"
MMPROJ_FILE="$(echo "$FILES" | sed -n 2p)"
if [ -z "$MODEL_FILE" ] || [ -z "$MMPROJ_FILE" ]; then
  echo "ERROR: could not find a GGUF model + mmproj in $HF_REPO"; exit 1
fi
for f in "$MODEL_FILE" "$MMPROJ_FILE"; do
  if [ -f "models/$f" ]; then echo "already downloaded: $f"; continue; fi
  echo "downloading $f …"
  curl -L --fail --retry 5 -C - -o "models/$f.part" "https://huggingface.co/$HF_REPO/resolve/main/$f"
  if [ "$(head -c 4 "models/$f.part")" != "GGUF" ]; then echo "ERROR: $f is not a GGUF file"; exit 1; fi
  mv "models/$f.part" "models/$f"
done

say "4/5  llama-server service"
scripts/install_llama_service.sh "$REPO/models/$MODEL_FILE" 3 4096 "$BIN" "$REPO/models/$MMPROJ_FILE"

say "5/5  Configure STRIX-1"
[ -f .env ] || cp .env.example .env
set_env() { if grep -q "^$1=" .env; then sed -i "s|^$1=.*|$1=$2|" .env; else echo "$1=$2" >> .env; fi; }
set_env QWEN_ENABLED true
set_env QWEN_SERVER_URL http://127.0.0.1:8080
set_env QWEN_MANAGE_SERVER auto
set_env QWEN_MODEL_ID "${MODEL_FILE%.gguf}"
set_env QWEN_MODEL_PATH "$REPO/models/$MODEL_FILE"
set_env QWEN_MMPROJ_PATH "$REPO/models/$MMPROJ_FILE"
if systemctl is-enabled --quiet strix1 2>/dev/null; then sudo systemctl restart strix1; echo "restarted strix1"; fi

say "Waiting for Qwen to load (up to 3 minutes)…"
for _ in $(seq 1 90); do
  if curl -fs http://127.0.0.1:8080/health >/dev/null 2>&1; then
    echo "Qwen is ready."
    curl -fs http://127.0.0.1:8080/props | python3 -c 'import json,sys; d=json.load(sys.stdin); print("vision support:", (d.get("modalities") or {}).get("vision"))' || true
    break
  fi
  sleep 2
done
echo
echo "Done. Open the dashboard: the QWEN row in STATUS should be green ('ready · sees images'). It now starts by itself on every boot."
echo "If STRIX-1 is not installed as a service, restart it now: scripts/run_backend.sh"
echo "llama-server logs: journalctl -u strix1-llama -f"
