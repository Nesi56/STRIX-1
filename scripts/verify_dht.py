#!/usr/bin/env python3
"""Verify the DHT11 (DATA → GPIO4 / physical pin 7, VCC → pin 17, GND → pin 9).

Tries the kernel IIO driver (needs `dtoverlay=dht11,gpiopin=4`), then the
Adafruit library, polling every 2.5 s, and reports the success rate:

    backend/.venv/bin/python scripts/verify_dht.py [--reads 6] [--backend kernel|adafruit]
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reads", type=int, default=6)
    ap.add_argument("--backend", choices=["auto", "kernel", "adafruit"], default="auto")
    ap.add_argument("--gpio", type=int, default=4)
    a = ap.parse_args()
    from app.services.dht_service import AdafruitDHT, KernelIIODHT, find_dht_iio_device

    dev = None
    if a.backend in ("auto", "kernel"):
        path = find_dht_iio_device()
        print(f"info IIO dht11 device: {path or 'not found (add dtoverlay=dht11,gpiopin=4 and reboot)'}")
        if path:
            dev = KernelIIODHT(str(path))
    if dev is None and a.backend in ("auto", "adafruit"):
        try:
            dev = AdafruitDHT(a.gpio)
            print("info using adafruit_dht (bit-banged; expect occasional failures on Pi 5)")
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL adafruit_dht unavailable: {exc}")
    if dev is None:
        print("FAIL no DHT11 backend available")
        return 1
    good = 0
    for i in range(a.reads):
        try:
            t, h = dev.read()
            good += 1
            print(f"ok   read {i + 1}: {t:.1f} °C  {h:.0f} %RH")
        except Exception as exc:  # noqa: BLE001
            print(f"WARN read {i + 1} failed: {exc}")
        time.sleep(2.5)
    print(f"RESULT {good}/{a.reads} successful reads", "PASS" if good >= a.reads // 2 else "FAIL")
    return 0 if good >= a.reads // 2 else 1


if __name__ == "__main__":
    sys.exit(main())
