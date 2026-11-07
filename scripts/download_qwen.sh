#!/usr/bin/env bash
# Download a quantized Qwen GGUF for local inference (resumable) and verify it.
# Usage: scripts/download_qwen.sh [hf-repo] [file]
#   default: Qwen/Qwen2.5-3B-Instruct-GGUF  qwen2.5-3b-instruct-q4_k_m.gguf  (~2.0 GB, Q4_K_M)
# Check the exact file name on the model's "Files" page if you choose another model.
# Alternative without this script: let llama-server fetch it: llama-server -hf Qwen/Qwen2.5-3B-Instruct-GGUF:Q4_K_M
set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HF_REPO="${1:-Qwen/Qwen2.5-3B-Instruct-GGUF}"
FILE="${2:-qwen2.5-3b-instruct-q4_k_m.gguf}"
DEST="$REPO_DIR/models"
mkdir -p "$DEST"
URL="https://huggingface.co/${HF_REPO}/resolve/main/${FILE}"
echo "Downloading $URL → $DEST/$FILE"
curl -L --fail --retry 5 --retry-delay 5 -C - -o "$DEST/$FILE" "$URL"
MAGIC="$(head -c 4 "$DEST/$FILE")"
if [ "$MAGIC" != "GGUF" ]; then echo "ERROR: $FILE is not a GGUF file (got '$MAGIC')"; exit 1; fi
SIZE=$(stat -c %s "$DEST/$FILE")
echo "OK: $(numfmt --to=iec "$SIZE") GGUF"
echo
echo "Add to .env:"
echo "  QWEN_MODEL_PATH=$DEST/$FILE"
echo "  QWEN_MODEL_ID=${FILE%.gguf}"
