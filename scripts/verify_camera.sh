#!/usr/bin/env bash
# Verify the Camera Module 3 Wide on the Pi 5 (CSI ribbon in CAM/DISP 0 or 1).
# Lists cameras, sensor modes, then measures achieved FPS for 1080p30, 720p60
# and 1080p60 requests with Picamera2 (the 60 fps claims are validated here).
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$REPO/backend/.venv/bin/python"; [ -x "$PY" ] || PY=python3
echo "== rpicam-hello --list-cameras"
if command -v rpicam-hello >/dev/null; then rpicam-hello --list-cameras || true
elif command -v libcamera-hello >/dev/null; then libcamera-hello --list-cameras || true
else echo "rpicam-apps not installed (sudo apt install rpicam-apps)"; fi
echo
echo "== Picamera2 sensor modes and FPS test (stop the strix1 service first: sudo systemctl stop strix1)"
"$PY" - <<'PY'
import time
try:
    from picamera2 import Picamera2
except ImportError as e:
    raise SystemExit(f"FAIL picamera2 not importable: {e} (apt install python3-picamera2; venv needs --system-site-packages)")
info = Picamera2.global_camera_info()
if not info:
    raise SystemExit("FAIL no camera detected — check the ribbon cable orientation and seating")
print("camera:", info[0])
cam = Picamera2()
for m in cam.sensor_modes:
    print(f"  mode {m['size'][0]}x{m['size'][1]} {m['bit_depth']}-bit up to {m['fps']:.1f} fps")
def test(w, h, fps, n=90):
    ok = [m for m in cam.sensor_modes if m["size"][0] >= w and m["size"][1] >= h and m["fps"] >= fps - 0.5]
    if not ok:
        print(f"  {w}x{h}@{fps}: NOT SUPPORTED by any sensor mode"); return
    mode = min(ok, key=lambda m: m["size"][0] * m["size"][1])
    cfg = cam.create_video_configuration(main={"size": (w, h), "format": "YUV420"},
                                         sensor={"output_size": mode["size"], "bit_depth": mode["bit_depth"]},
                                         controls={"FrameRate": float(fps)})
    cam.configure(cfg); cam.start(); time.sleep(1.0)
    t0 = time.monotonic()
    for _ in range(n):
        cam.capture_request().release()
    got = n / (time.monotonic() - t0)
    cam.stop()
    print(f"  {w}x{h}@{fps}: measured {got:.1f} fps  {'PASS' if got >= fps * 0.9 else 'BELOW TARGET'}")
test(1920, 1080, 30)
test(1280, 720, 60)
test(1920, 1080, 60)
cam.close()
PY
