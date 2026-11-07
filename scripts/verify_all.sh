#!/usr/bin/env bash
# Run the non-interactive hardware checks in sequence (servo test is interactive: run it separately).
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$REPO/backend/.venv/bin/python"
echo "### I2C bus scan (MPU6050 should appear at 68)"; i2cdetect -y 1 2>/dev/null || echo "i2cdetect unavailable / I2C disabled"
echo; echo "### IMU";    "$PY" "$REPO/scripts/verify_imu.py"
echo; echo "### DHT11";  "$PY" "$REPO/scripts/verify_dht.py" --reads 4
echo; echo "### Camera"; "$REPO/scripts/verify_camera.sh"
echo; echo "### Power";  "$REPO/scripts/verify_power.sh"
echo; echo "Servo: run interactively →  $PY $REPO/scripts/verify_servo.py"
