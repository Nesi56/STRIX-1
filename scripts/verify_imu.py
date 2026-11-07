#!/usr/bin/env python3
"""Verify the MPU6050 on I2C bus 1 (GPIO2 SDA / GPIO3 SCL, physical pins 3/5).

Checks: I2C bus present, device ACK at the address, WHO_AM_I == 0x68, live
samples, sample rate, |a| ≈ 1 g at rest, gyro bias. Uses the same burst-read
driver as the backend. Run on the Pi with the airframe still:

    backend/.venv/bin/python scripts/verify_imu.py [--address 0x68] [--seconds 3]
"""
import argparse
import math
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--address", default="0x68")
    ap.add_argument("--seconds", type=float, default=3.0)
    a = ap.parse_args()
    addr = int(a.address, 0)
    ok = True
    if not os.path.exists("/dev/i2c-1"):
        print("FAIL /dev/i2c-1 missing — enable I2C: sudo raspi-config nonint do_i2c 0 (then reboot)")
        return 1
    print("ok   /dev/i2c-1 present")
    try:
        from smbus2 import SMBus  # optional, for a raw WHO_AM_I check
        with SMBus(1) as bus:
            who = bus.read_byte_data(addr, 0x75)
        print(f"{'ok  ' if who == 0x68 else 'WARN'} WHO_AM_I = {who:#04x} (expected 0x68; some clones report 0x70/0x98)")
    except ImportError:
        print("info smbus2 not installed; skipping raw WHO_AM_I read (Adafruit driver checks it below)")
    except OSError as exc:
        print(f"FAIL no ACK from {addr:#04x}: {exc}. Check wiring (VIN→pin 1 3.3V, GND→pin 6, SDA→pin 3, SCL→pin 5) and `i2cdetect -y 1`.")
        return 1

    from app.config.settings import Settings
    from app.services.imu_service import AdafruitMPU6050Backend

    s = Settings(_env_file=None, imu_address=addr)
    try:
        dev = AdafruitMPU6050Backend(s)
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL Adafruit MPU6050 init: {exc}")
        return 1
    print("ok   driver initialised (±4 g, ±500 °/s, DLPF 44 Hz)")
    acc, gyr, temps, t0 = [], [], [], time.monotonic()
    while time.monotonic() - t0 < a.seconds:
        x, g, t = dev.read()
        acc.append(x); gyr.append(g); temps.append(t)
        time.sleep(0.01)
    n = len(acc)
    rate = n / (time.monotonic() - t0)
    norm = sum(math.sqrt(sum(v * v for v in x)) for x in acc) / n / 9.80665
    bias = [sum(g[i] for g in gyr) / n for i in range(3)]
    std = max(math.sqrt(sum((g[i] - bias[i]) ** 2 for g in gyr) / n) for i in range(3))
    print(f"info {n} samples at {rate:.0f} Hz")
    print(f"{'ok  ' if 0.9 < norm < 1.1 else 'WARN'} mean |a| = {norm:.3f} g (expect ≈1.000 at rest)")
    print(f"info gyro mean (bias) °/s = {bias[0]:+.2f} {bias[1]:+.2f} {bias[2]:+.2f}; noise σ ≈ {std:.3f} °/s")
    print(f"info die temperature ≈ {sum(temps) / n:.1f} °C")
    print(f"info last accel m/s² = {acc[-1][0]:+.2f} {acc[-1][1]:+.2f} {acc[-1][2]:+.2f}")
    if std > 1.0:
        print("WARN gyro noise high — was the airframe moving?")
    ok = ok and 0.9 < norm < 1.1
    print("RESULT", "PASS" if ok else "CHECK WARNINGS")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
