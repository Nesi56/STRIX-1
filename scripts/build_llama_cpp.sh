#!/usr/bin/env bash
# Build llama.cpp's llama-server natively on the Raspberry Pi 5 (Cortex-A76, NEON/dotprod).
# Usage: scripts/build_llama_cpp.sh [install-dir]   (default: ~/llama.cpp)
# Env:   LLAMA_CPP_REF=<tag or commit> to pin a release (recommended for reproducibility).
# Takes ~10-20 min on a Pi 5. Requires ~2 GB free disk.
set -euo pipefail
DIR="${1:-$HOME/llama.cpp}"
REF="${LLAMA_CPP_REF:-}"
echo "Packages needed: git cmake build-essential libcurl4-openssl-dev (curl enables 'llama-server -hf <repo>')"
read -r -p "Install them with apt now? [y/N] " r
[[ "$r" =~ ^[Yy]$ ]] && sudo apt-get update && sudo apt-get install -y git cmake build-essential libcurl4-openssl-dev
if [ ! -d "$DIR/.git" ]; then git clone https://github.com/ggml-org/llama.cpp "$DIR"; fi
cd "$DIR"
git fetch --tags --quiet
if [ -n "$REF" ]; then git checkout --quiet "$REF"; else git pull --ff-only --quiet || true; fi
echo "Building llama.cpp $(git describe --tags --always)"
cmake -B build -DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=ON
cmake --build build --config Release -j"$(nproc)" --target llama-server
echo
echo "Built: $DIR/build/bin/llama-server"
echo "Set in .env:  QWEN_LLAMA_SERVER_BIN=$DIR/build/bin/llama-server"
