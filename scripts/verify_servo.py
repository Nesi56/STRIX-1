#!/usr/bin/env python3
"""Interactive camera-servo check (SG90 signal → GPIO18 / physical pin 12).

SAFETY: the servo must be powered from a regulated external 5 V supply with a
common ground to the Pi — never from a GPIO pin. Remove the camera or make
sure the mount cannot hit the frame. Only the camera servo is driven.

    backend/.venv/bin/python scripts/verify_servo.py [--backend gpiozero|hardware_pwm] [--min 60 --max 120]
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=["gpiozero", "hardware_pwm"], default="gpiozero")
    ap.add_argument("--min", type=float, default=60.0, help="lowest test angle (deg)")
    ap.add_argument("--max", type=float, default=120.0, help="highest test angle (deg)")
    ap.add_argument("--chip", type=int, default=0)
    ap.add_argument("--channel", type=int, default=2)
    a = ap.parse_args()
    print(__doc__)
    if input("Servo powered externally, common ground, mount clear? type 'yes': ").strip() != "yes":
        print("aborted")
        return 1
    from app.config.settings import Settings
    from app.services.servo_service import GpiozeroServoBackend, SysfsPWMServoBackend, angle_to_pulse_us, explain_gpio_error

    s = Settings(_env_file=None, servo_backend=a.backend, servo_pwm_chip=a.chip, servo_pwm_channel=a.channel)
    try:
        dev = SysfsPWMServoBackend(s) if a.backend == "hardware_pwm" else GpiozeroServoBackend(s)
    except Exception as exc:
        print(f"FAIL: cannot drive GPIO{s.servo_gpio} (pin 12): {explain_gpio_error(exc, s.servo_gpio)}")
        return 1
    try:
        for angle in (90, a.min, 90, a.max, 90):
            pulse = angle_to_pulse_us(angle, s.servo_min_pulse_us, s.servo_max_pulse_us)
            print(f"→ {angle:5.1f}°  ({pulse:.0f} µs)")
            dev.write_pulse_us(pulse)
            time.sleep(1.2)
        dev.detach()
        print("RESULT done — confirm the camera moved smoothly to each position and back to center.")
        print("If it hit an end-stop or buzzed, narrow STRIX_SERVO_MIN/MAX_ANGLE or adjust STRIX_SERVO_*_PULSE_US.")
    finally:
        dev.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
