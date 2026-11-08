#!/usr/bin/env bash
# Install an object-detection model for the "YOLO stream" into <repo>/models/.
#
#   scripts/get_detector_model.sh             # default: YOLOX-Tiny (Apache-2.0, 20 MB, fast, 80 everyday objects)
#   scripts/get_detector_model.sh --everyday  # YOLOv8n Open Images V7: 600 objects incl. pens, phones, mugs
#                                             #   (exported here with Ultralytics, AGPL-3.0; temporary ~2 GB download)
#   scripts/get_detector_model.sh --yolox-s   # YOLOX-S (more accurate, ~4x more CPU, runs the Pi hotter)
#   scripts/get_detector_model.sh --yolo11n   # Ultralytics YOLO11n (AGPL-3.0), 80 objects
#
# YOLOX models are downloaded directly (no PyTorch) and verified by SHA-256. STRIX-1 picks the model
# automatically (everyday > YOLO11/YOLOv8 > YOLOX-Tiny > YOLOX-S); restart STRIX-1 afterwards.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$REPO/models"
mkdir -p "$DEST"

fetch() {  # url sha256 output
  echo "Downloading $(basename "$3") → $3"
  curl -L --fail --retry 3 -o "$3.part" "$1"
  local got; got="$(sha256sum "$3.part" | cut -d' ' -f1)"
  if [ "$got" != "$2" ]; then
    echo "ERROR: checksum mismatch ($got). The upstream file may have changed; not installing."
    rm -f "$3.part"; exit 1
  fi
  mv "$3.part" "$3"
  echo "OK: $3 (checksum verified)"
}

export_ultralytics() {  # weights imgsz
  local TMP; TMP="$(mktemp -d)"
  echo "Exporting $1 (imgsz=$2) with a temporary virtualenv in $TMP (CPU-only PyTorch)…"
  python3 -m venv "$TMP/venv"
  "$TMP/venv/bin/pip" install -q --index-url https://download.pytorch.org/whl/cpu torch torchvision
  "$TMP/venv/bin/pip" install -q ultralytics onnx onnxslim
  (cd "$TMP" && "$TMP/venv/bin/python" - "$1" "$2" <<'PY'
import sys
from ultralytics import YOLO
m = YOLO(sys.argv[1])
m.export(format="onnx", imgsz=int(sys.argv[2]), opset=12, simplify=True)
stem = sys.argv[1].rsplit(".", 1)[0]
with open(f"{stem}.labels.txt", "w") as f:
    f.write("\n".join(m.names[i] for i in range(len(m.names))) + "\n")
PY
  )
  local stem="${1%.pt}"
  cp "$TMP/$stem.onnx" "$TMP/$stem.labels.txt" "$DEST/"
  rm -rf "$TMP"
  echo "OK: $DEST/$stem.onnx (+ $stem.labels.txt)"
}

case "${1:-}" in
  --everyday) export_ultralytics yolov8n-oiv7.pt "${2:-416}" ;;
  --yolo11n)  export_ultralytics yolo11n.pt "${2:-416}" ;;
  --yolox-s)
    fetch "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/object_detection_yolox/object_detection_yolox_2022nov.onnx" \
          c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063 "$DEST/yolox_s.onnx" ;;
  ""|--yolox-tiny)
    fetch "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_tiny.onnx" \
          427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7 "$DEST/yolox_tiny.onnx" ;;
  *) echo "unknown option: $1"; exit 2 ;;
esac
echo "Restart STRIX-1 — the detector picks the model up automatically."
