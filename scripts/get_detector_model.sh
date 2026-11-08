#!/usr/bin/env bash
# Install an object-detection model for the "YOLO stream" into <repo>/models/.
#
#   scripts/get_detector_model.sh            # default: YOLOX-S (OpenCV Zoo, Apache-2.0, ~36 MB)
#   scripts/get_detector_model.sh --yolo11n  # optional: export Ultralytics YOLO11n (AGPL-3.0) to ONNX
#
# YOLOX-S is downloaded directly (no PyTorch needed) and verified by SHA-256. It is the
# model STRIX-1 was tested with. YOLO11n is ~4x lighter (faster on the Pi) but must be
# exported with the ultralytics package in a throw-away virtualenv (needs ~1-2 GB of
# temporary disk; works on a laptop or the Pi). Note its AGPL-3.0 license.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$REPO/models"
mkdir -p "$DEST"

if [ "${1:-}" = "--yolo11n" ]; then
  IMGSZ="${2:-416}"
  TMP="$(mktemp -d)"
  echo "Exporting YOLO11n (imgsz=$IMGSZ) using a temporary venv in $TMP"
  python3 -m venv "$TMP/venv"
  "$TMP/venv/bin/pip" install -q ultralytics onnx onnxslim
  (cd "$TMP" && "$TMP/venv/bin/yolo" export model=yolo11n.pt format=onnx imgsz="$IMGSZ")
  cp "$TMP/yolo11n.onnx" "$DEST/yolo11n.onnx"
  rm -rf "$TMP"
  echo "OK: $DEST/yolo11n.onnx (picked automatically; it takes priority over yolox)"
  exit 0
fi

URL="https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/object_detection_yolox/object_detection_yolox_2022nov.onnx"
SHA="c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063"
OUT="$DEST/yolox_s.onnx"
echo "Downloading YOLOX-S (OpenCV Zoo, Apache-2.0) → $OUT"
curl -L --fail --retry 3 -o "$OUT.part" "$URL"
GOT="$(sha256sum "$OUT.part" | cut -d' ' -f1)"
if [ "$GOT" != "$SHA" ]; then
  echo "ERROR: checksum mismatch ($GOT). The upstream file may have changed; not installing."
  rm -f "$OUT.part"
  exit 1
fi
mv "$OUT.part" "$OUT"
echo "OK: $OUT (checksum verified). Restart STRIX-1 — the detector picks it up automatically."
