# STRIX-1 — Raspberry Pi 5 AI Drone Ground Station

STRIX-1 is an **observation, diagnostics and research** platform for a 250 mm FPV quadcopter
(SpeedyFPV ZMR250). A Raspberry Pi 5 (16 GB) on the airframe reads its sensors, streams the
Camera Module 3 Wide, runs local AI inference and serves a browser dashboard. You open it on
your laptop over Wi-Fi — no VNC, no monitor.

> **Scope and safety.** STRIX-1 does **not** implement flight control, motor commands, navigation
> setpoints, target tracking or any automatic movement of the aircraft. The only actuator it
> drives is the one-axis **camera tilt servo**, which starts disarmed and is limited and rate-limited.
> It never talks to or reconfigures a flight controller.

![dashboard](docs/img/dashboard.png)

## Features

| Area | What you get |
|---|---|
| **Live camera** | Low-latency MJPEG stream (fetch-based reader that always shows the newest frame). Shows browser FPS, camera FPS, measured **glass-to-browser latency** (sensor timestamps + clock sync) and bitrate. Also: snapshots, H.264/MP4 recording, fullscreen, resolution/FPS selection limited to modes the sensor supports, AI detection overlay, and optional WebRTC via MediaMTX. |
| **IMU (MPU6050)** | 100 Hz burst-read acquisition, complementary filter for roll/pitch, relative yaw, scrolling accel/gyro charts, calibration (gyro bias + level reference), health checks (stale data, low rate, saturation, stuck sensor, bias, implausible \|a\|). The UI states clearly that yaw is relative and drifts (no magnetometer). |
| **3D orientation** | React Three Fiber model of a 250 mm quad, attitude indicator, horizon grid, orbit/zoom, reset view, zero yaw, recalibrate, camera-tilt visualization. |
| **Environment (DHT11)** | Temperature (°C/°F), humidity, history chart, session min/max, failure and stale-data warnings; reads every 3 s. |
| **Camera servo (SG90)** | Enable/disable control, slider, center, ±5° nudges, safe limits, 60 °/s slew limit. Angles are labeled *commanded* because the SG90 has no position feedback. |
| **Map / environment** | **A** orientation at a fixed origin · **B** OpenCV visual observations (vegetation, sky, structures, paths, people), image-relative only · **C** experimental monocular visual odometry with tracking quality and drift indicators. Without a validated localization source the map always shows *"Position unavailable — no validated localization source."* |
| **AI assistant** | Local **Qwen** via llama.cpp, optional **Grok** (xAI) through a backend proxy, and **Hybrid** mode. Answers are grounded in timestamped telemetry and tagged `[MEASURED]` / `[INTERPRETATION]` / `[UNAVAILABLE]`. Anything sent to the cloud requires explicit consent. Shell commands are filtered out of model output. If no model is available, a deterministic telemetry digest answers instead. |
| **System health** | CPU/RAM/temperature, throttling and under-voltage (`vcgencmd`), Pi 5 PMIC 5 V input, Wi-Fi signal, link RTT, AI tokens/s. **Battery % is shown as unavailable**: the power bank reports no telemetry, so it is never estimated. |
| **Recording** | SQLite telemetry sessions (10 Hz), playback with a timeline, CSV/JSON export, retention, plus video and snapshot library and event/log viewer. |
| **Access** | One port (`:8000`), token login → HttpOnly session cookie, private-network-only guard, WebSocket auto-reconnect with connection-loss banner. |
| **Demo mode** | `STRIX_MOCK=true` gives realistic, **clearly labeled synthetic** data for every device. Synthetic data is never substituted for real measurements: a missing device shows *disconnected*. |

## Quick start (no hardware, any Linux/macOS laptop)

```bash
git clone https://github.com/Nesi56/STRIX-1.git && cd STRIX-1
cd frontend && npm ci && npm run build && cd ..        # Node ≥ 20.19
scripts/run_mock.sh                                     # prints the access token
```

Open <http://localhost:8000> and enter the printed token. Everything runs on synthetic data
marked **SYNTHETIC** in the UI.

For development with hot reload, run `scripts/run_dev.sh` (backend on :8000, Vite on :5173).

## Installing on the Raspberry Pi 5

Full guide: **[docs/SETUP_PI.md](docs/SETUP_PI.md)** (OS image, I2C, overlays, camera checks, Qwen, Grok, systemd).
Wiring: **[docs/HARDWARE.md](docs/HARDWARE.md)**.

```bash
git clone https://github.com/Nesi56/STRIX-1.git ~/STRIX-1 && cd ~/STRIX-1
scripts/install_pi.sh                                    # packages, venv, .env + token, dashboard build
scripts/install_pi.sh --configure-boot --groups          # optional: I2C + DHT11 overlay + groups (backs up config.txt)
sudo reboot
scripts/verify_all.sh                                    # IMU, DHT11, camera, power checks
scripts/install_pi.sh --skip-apt --skip-frontend --systemd   # autostart on boot
```

Then browse from your laptop on the same Wi-Fi to **`http://<hostname>.local:8000`**
(for example `http://raspberrypi.local:8000`) or `http://<pi-ip>:8000`.

## Wiring summary (BCM numbering in code)

| Device | Signal | Pi physical pin | BCM |
|---|---|---|---|
| MPU6050 | VIN (3.3 V) / GND / SDA / SCL | 1 / 6 / 3 / 5 | — / — / GPIO2 / GPIO3 |
| DHT11 | VCC (3.3 V) / DATA / GND | 17 / 7 / 9 | — / GPIO4 / — |
| SG90 | Signal | 12 | GPIO18 |
| SG90 | +5 V / GND | **external regulated 5 V** / common ground | — |
| Camera Module 3 Wide | CSI ribbon | CAM/DISP connector | — |

Never power the servo from a GPIO pin. Check the actual 52Pi PD board revision and its
regulated 5 V output rating before taking servo power from it (see docs/HARDWARE.md).

## Architecture

```
 Raspberry Pi 5 ─────────────────────────────────────────────────────────────────────────
  threads (blocking I/O)                asyncio (FastAPI / uvicorn)                 browser
  ┌─────────────────────┐   latest +    ┌──────────────────────────┐  /ws/telemetry ┌──────────┐
  │ imu_service  100 Hz ├──ring buffer──▶ telemetry_service 20 Hz  ├───────────────▶│ React UI │
  │ dht_service   0.3 Hz│               │  (bounded per-client Q)  │                │  R3F 3D  │
  │ servo_service 50 Hz │◀─commands─────┤ api.py (REST, validated) │◀──── REST ─────┤  charts  │
  │ system_monitor 0.5Hz│               │ recording_service→SQLite │                │  AI dock │
  │ camera (Picamera2   │──JPEG hub────▶│ /api/camera/stream.mjpg  ├── MJPEG ──────▶│          │
  │  encoder threads)   │──lores frame─▶│ mapping_service (OpenCV) │                └──────────┘
  └─────────────────────┘               │ ai_service → Qwen (llama-server :8080, local)        │
                                        │            → Grok (api.x.ai, explicit consent only)  │
                                        └──────────────────────────┘
```

Sensors, camera, recording and AI run independently. A failed camera, a loading LLM or a
cloud outage never stops sensor acquisition. Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Project structure

```
backend/           FastAPI app (Python 3.11+)
  app/api/         api.py (REST), websocket.py, auth.py, network_guard.py
  app/services/    camera_service, imu_service, dht_service, servo_service, telemetry_service,
                   mapping_service (+ vision.py), ai_qwen_service, ai_grok_service, ai_service,
                   ai_context, recording_service, system_monitor, container
  app/core/        orientation filter, rate limiting, output safety, logging
  app/database/    SQLite layer        app/models/  request schemas    app/config/  settings
  tests/           pytest suite (mock mode)
frontend/          React 19 + TypeScript + Vite + Tailwind 4 + React Three Fiber + Recharts + Lucide
scripts/           install, run, verification, llama.cpp/Qwen helpers
config/            systemd units, config.txt overlay block, MediaMTX config
docs/              setup, hardware, AI, streaming, API, troubleshooting, verification matrix
```

## Testing

```bash
scripts/run_tests.sh      # backend pytest (104 tests) + frontend typecheck, vitest, production build
```

The automated tests cover:
- sensor data parsing (MPU6050 burst, IIO DHT11, `vcgencmd`, PMIC, `/proc/net/wireless`, `.env`)
- orientation estimation and calibration
- mock telemetry consistency
- WebSocket protocol and authentication
- REST validation and errors
- recording, playback, export and retention
- AI grounding, consent, error handling, rate limits and output filtering (using simulated llama-server and xAI endpoints)
- servo limits and slew rate
- vision and VO
- the MJPEG multipart parser and clock sync

## Verified vs. unverified

This repository was developed and tested **without the physical hardware** (x86-64 Linux, mock
mode). Hardware code paths were written against the actual library sources (Picamera2 0.3.39,
Adafruit MPU6050 1.3.9, Adafruit DHT 4.0.12, Blinka 10.1, gpiozero 2.0, llama.cpp server API).
They still **must be validated on the Pi**. See **[docs/HARDWARE_VERIFICATION.md](docs/HARDWARE_VERIFICATION.md)**
for the full matrix and step-by-step checks. Highlights:

- ✅ Verified here: full dashboard and API in mock mode, WebSocket reconnect, recording/export,
  AI routing/consent/fallbacks (simulated servers), OpenCV analysis, VO on rendered scenes, auth.
- ⚠️ Needs Pi validation:
  - real MPU6050/DHT11/SG90 I/O
  - Picamera2 streaming, recording and achieved FPS. 1080p60 is **not** offered: the IMX708's 2304×1296 mode tops out near 56 fps.
  - CPU budget with software H.264 (the Pi 5 has no hardware video encoder)
  - llama.cpp throughput, `vcgencmd`/PMIC parsing on your firmware
  - the 52Pi board's servo power capacity
  - the optional WebRTC path

## Documentation

- [docs/SETUP_PI.md](docs/SETUP_PI.md) — Raspberry Pi OS setup, install, systemd, LAN access
- [docs/HARDWARE.md](docs/HARDWARE.md) — wiring, power, overlays, hardware PWM
- [docs/AI.md](docs/AI.md) — Qwen/llama.cpp, Grok, hybrid mode, grounding, privacy
- [docs/STREAMING.md](docs/STREAMING.md) — MJPEG vs WebRTC, latency measurement, CPU budget
- [docs/API.md](docs/API.md) — REST endpoints and WebSocket protocol
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — modules, threading, safety design
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)
- [docs/HARDWARE_VERIFICATION.md](docs/HARDWARE_VERIFICATION.md)

## License

MIT — see [LICENSE](LICENSE).
