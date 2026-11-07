# Verified vs. unverified features

STRIX-1 was developed **without access to the physical hardware**, in an x86-64 Linux container
(Python 3.13, Node 22, headless Chromium). Everything listed as verified ran there in mock mode
or against simulated servers. Hardware paths were written against the actual source of the
libraries (Picamera2 0.3.39, adafruit-circuitpython-mpu6050 1.3.9, adafruit-circuitpython-dht
4.0.12, Adafruit Blinka 10.1.0, gpiozero 2.0.1, rpi-hardware-pwm 0.3.1, llama.cpp server
README), but **they have not run on a Raspberry Pi 5 yet**. Fill in the right-hand column as
you validate.

| Feature | Verified here | Needs validation on the Pi 5 | Your result |
|---|---|---|---|
| Backend startup, graceful degradation when devices are missing | ✅ (missing hardware → *disconnected*, no fake data) | — | |
| Dashboard (all pages), desktop + mobile layout | ✅ Playwright, 1600×1000 and 390×844 | real browsers on your laptop/phone | |
| Token login, cookie session, LAN-only guard, WS origin check | ✅ tests | — | |
| WebSocket telemetry 20 Hz + reconnect banner | ✅ | Wi-Fi drop/rejoin behaviour | |
| MPU6050 burst read + scaling | ✅ unit test of the decoder | `scripts/verify_imu.py`: WHO_AM_I, \|a\| ≈ 1 g, ~100 Hz | |
| Complementary filter, sign conventions | ✅ unit tests | nose-up / right-down checks on the airframe | |
| IMU calibration | ✅ (synthetic) | bias values and stillness thresholds on real hardware | |
| DHT11 kernel IIO backend | ✅ against a fake sysfs tree | `dtoverlay=dht11,gpiopin=4` + `scripts/verify_dht.py` | |
| DHT11 Adafruit fallback | ✗ | reliability on the Pi 5 (expected to be poor) | |
| Servo slew/limits/arming logic | ✅ tests (mock backend) | — | |
| Servo gpiozero output on GPIO18 | ✗ | `scripts/verify_servo.py`, jitter level | |
| Servo hardware PWM (sysfs) | ✗ | overlay, chip/channel discovery (HARDWARE.md) | |
| 52Pi PD board servo power capacity | ✗ | board revision + 5 V rail rating | |
| Picamera2 MJPEG streaming + sensor timestamps | ✗ (mock camera only) | `scripts/verify_camera.sh`, then dashboard latency | |
| 1080p30 stream | ✗ | measured FPS + CPU with/without recording | |
| 720p60 (offered) / 1080p60 (not offered) | logic ✅ | measured FPS at 720p60 | |
| H.264 MP4 recording (PyavOutput) | ✗ (mock uses OpenCV MP4) | file plays; streaming uninterrupted | |
| Autofocus (Camera Module 3) | ✗ | continuous AF behaviour on the airframe | |
| WebRTC via MediaMTX | ✗ | experimental path end to end | |
| System monitor (psutil, thermal zone) | ✅ (x86) | Pi thermal zone name | |
| `vcgencmd get_throttled` / `pmic_read_adc` parsing | ✅ unit tests with documented output format | output format on your firmware | |
| OpenCV scene analysis (heuristics, HOG people) | ✅ synthetic frames | usefulness on real aerial imagery | |
| Monocular VO | ✅ rendered 3D scene recovers direction | real camera (lens distortion, motion blur) | |
| Recording, playback, CSV/JSON export, retention | ✅ tests + browser | SD-card write load over long sessions | |
| Local Qwen via llama-server | ✅ protocol against a simulated server (SSE, timings, /health 503, /props) | build, load time, tokens/s, memory | |
| Grok proxy (consent, rate limits, errors, cost) | ✅ simulated xAI endpoint | real API key, current model ids, `input_modalities` | |
| systemd units | ✗ | `install_pi.sh --systemd`, autostart after reboot | |
| Installer | syntax-checked only | full run on a fresh Raspberry Pi OS image | |

## Validation checklist (in order)

1. `scripts/verify_power.sh` under idle and under load → `throttled=0x0`.
2. `i2cdetect -y 1` → `68`; `scripts/verify_imu.py` → PASS.
3. Airframe checks in the dashboard: nose up → pitch +, right side down → roll +; rotate
   clockwise (seen from above) → relative yaw increases.
4. `scripts/verify_dht.py` → ≥ 50 % successful reads; compare with a reference thermometer
   (DHT11 is ±2 °C).
5. `scripts/verify_camera.sh` → note the measured FPS for each mode.
6. Dashboard: stream at 1080p30, note browser FPS + latency; start a recording, confirm the
   stream continues and the MP4 plays; repeat while Qwen is answering.
7. `scripts/verify_servo.py` with external 5 V; then set safe limits in Settings.
8. Qwen: `curl 127.0.0.1:8080/health`, ask the six example questions, note tokens/s.
9. Grok (optional): one consented request; confirm the cost/usage display.
10. Pull the IMU connector while running → *disconnected* within ~1 s, other panels keep
    working; reconnect → recovers within ~5 s.
11. Turn Wi-Fi off/on on the laptop → banner, then automatic recovery.
