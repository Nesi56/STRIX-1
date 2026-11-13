# STRIX-1 — AI camera operator for a Raspberry Pi 5 drone

STRIX-1 turns a **Raspberry Pi 5** on a 250 mm FPV quadcopter (SpeedyFPV ZMR250) into an AI camera
operator. A **local Qwen vision model** looks at the camera image and **identifies** what it sees,
reacts to **YOLO** detections, reads the **MPU6050** (motion) and **DHT11** (temperature/humidity),
**aims the camera with a servo**, **estimates distances**, **remembers objects it can no longer see —
even a ball hidden under a shuffled cup —** and **suggests how to move**. Everything is on one screen in
any browser on the same Wi-Fi; no VNC or monitor needed.

> **Several drones?** Run the self-hosted Rust hub on a laptop: [docs/HUB.md](docs/HUB.md).
>
> **Judges:** start with **[docs/JUDGES.md](docs/JUDGES.md)** for a 3-minute demo script.

![console](docs/img/dashboard.png)

> **Safety scope.** The AI controls **only the one-axis camera tilt servo**, only for a task the
> operator gave it, within safe limits and a slew rate. EMERGENCY STOP (Space) turns it off and keeps
> it off until the operator turns it back on. STRIX-1 never sends motor, flight-controller or
> navigation commands. Movement advice is for the human pilot.

## How it works

```
                 ┌──────────────── Raspberry Pi 5 ────────────────────────────────────────┐
 Camera Module 3 ─▶ Picamera2 ─┬─▶ MJPEG stream ─────────────────────────────────────────┐ │
                               └─▶ YOLO (ONNX) ─▶ objects + distance + bearing ─┐       │ │
 MPU6050 ─▶ complementary filter ─▶ roll / pitch / yaw ──────────────────────────┤       │ │
 DHT11   ─▶ kernel IIO driver ────▶ temperature / humidity ──────────────────────┤       │ │
                                                                                  ▼       │ │
          Qwen (llama.cpp, local)  GENERATOR ─plan─▶ OBSERVER → PILOT → UNTIL ─▶ REPORT  │ │
                                                       │ (camera servo only)              │ │
 SG90 tilt servo ◀──────── ServoService (limits, slew, enable, e-stop) ◀──────┘          │ │
                 └────────────────────── FastAPI :8000 (REST + WebSocket) ───────────────┘─┘
                                                │ Wi-Fi
                                   Browser console (laptop / phone)
```

| Feature | Details |
|---|---|
| **Qwen co-pilot** | Turns your request into camera actions (`look`, `tilt`, `center`, `scan`, `find`, `track`, `goto`, `wait`) using schema-constrained JSON, so even a 2–3 B model always returns valid commands. It answers in short, plain sentences. If Qwen isn't running, a keyword planner and rule-based answers stand in. → [docs/AI.md](docs/AI.md) |
| **Qwen vision** | With the Qwen2.5-VL model (`scripts/setup_ai.sh`), "what do you see?" or IDENTIFY sends the current camera frame to Qwen, which describes it. It is not limited to YOLO's 80 classes. |
| **Object memory** | Every object is kept as a direction from the drone (IMU yaw/pitch/roll + servo tilt + image position) with an estimated range: **SEEN**, **REMEMBERED** (out of view / occluded, with an out-of-sight timer) or **HIDDEN** inside a cup or bowl, moving with it when the cups are shuffled. It re-identifies objects and corrects itself on reveal. → [docs/AI.md](docs/AI.md#object-memory-seen-vs-remembered-vs-hidden) |
| **Live 3D memory world** | The drone (driven by the MPU6050) with its camera view cone and every remembered object. Hidden objects glow *inside* see-through cups. Dashed boxes on the video show remembered objects behind or under things. |
| **Send to a remembered target** | "Where is the ball?" / AIM → the camera tilts back to the remembered object and Qwen tells the pilot which way to turn. The aircraft is never moved. |
| **Speaks up by itself** | Qwen comments when YOLO sees something new or an object goes under / comes out from a cup. |
| **YOLO stream** | YOLOX-Tiny by default: fast and light, 80 everyday objects incl. phones, cups and bottles. The optional **everyday model** (`--everyday`, YOLOv8n Open Images, 600 objects) adds pens, mugs and more. Green boxes show `cup 0.90 · ~0.7 m`. The row above the Qwen chat chooses what to look for. |
| **Distance & direction** | Pinhole model with typical object heights (HFOV 102°), labeled as estimates (±30 %). Bearing comes from image position; the radar shows a top-down view. |
| **Suggestions** | Computed from geometry and sensors, e.g. "Yaw right ~15°", "Advance ~3.9 m to a 1.5 m standoff", "Level the airframe first". |
| **Sensors** | MPU6050 at 100 Hz with a complementary filter (roll/pitch, *relative* yaw: no magnetometer), calibration and health checks. DHT11 through the Pi 5 kernel driver. |
| **Camera servo** | The original servo panel: **Enable control**, Center, −5°/+5° and an angle slider (keys: E enable, W/S tilt, C center, Space emergency stop). A task for Qwen turns control on by itself. SG90 signal on GPIO18 (pin 12), powered from the 52Pi board's 5 V. |
| **One-screen console** | Left: status (incl. CPU temperature), camera servo, memory list · center: live video · right: live 3D (world or drone close-up) above the Qwen chat with the YOLO targets, plus a recordings tab. **Start recording** saves the camera video and all sensor data together. |
| **Runs cool** | 720p camera stream, light detector, small Qwen model by default. YOLO slows down while Qwen thinks or the CPU passes 75 °C, and automatic comments skip the camera image. |
| **Demo mode** | `STRIX_MOCK=true` gives a synthetic camera (a **shell game** with three cups and a ball, or an outdoor scene), IMU and DHT with ground-truth detections, all labeled **SYNTHETIC**. A missing real device shows *disconnected*, never fake data. |

## Quick start (no hardware, any laptop)

```bash
git clone https://github.com/Nesi56/STRIX-1.git && cd STRIX-1
cd frontend && npm ci && npm run build && cd ..        # Node ≥ 20.19
scripts/run_mock.sh                                     # prints the access token
```

Open <http://localhost:8000> and sign in with the token. Watch the shell game: the ball goes under a cup and
the cups are shuffled. Ask Qwen *"look up 45 degrees"* to look away, then *"where is the ball?"*. For the person-tracking demo, switch the DEMO SCENE to **Outdoor** and ask
*"Find the person and keep them centered"*.

## On the Raspberry Pi 5

Full guide: **[docs/SETUP_PI.md](docs/SETUP_PI.md)** · wiring: **[docs/HARDWARE.md](docs/HARDWARE.md)**.

```bash
git clone https://github.com/Nesi56/STRIX-1.git ~/STRIX-1 && cd ~/STRIX-1
scripts/install_pi.sh                                    # packages, venv, .env + token, dashboard build
scripts/install_pi.sh --skip-apt --skip-frontend --configure-boot --groups && sudo reboot
scripts/setup_ai.sh                                      # YOLO + Qwen vision model, once; Qwen then starts by itself
scripts/verify_all.sh                                    # IMU, DHT11, camera, power checks
scripts/install_pi.sh --skip-apt --skip-frontend --systemd   # autostart
```

Then browse to **`http://<hostname>.local:8000`** from your laptop. Everything also works over SSH;
see SETUP_PI.md.

## Wiring summary (BCM numbering in code)

| Device | Signal | Pi physical pin | BCM |
|---|---|---|---|
| MPU6050 | VIN (3.3 V) / GND / SDA / SCL | 1 / 6 / 3 / 5 | — / — / GPIO2 / GPIO3 |
| DHT11 | VCC (3.3 V) / DATA / GND | 17 / 7 / 9 | — / GPIO4 / — |
| SG90 | Signal | 12 | GPIO18 |
| SG90 | +5 V / GND | **external regulated 5 V** / common ground | — |
| Camera Module 3 Wide | CSI ribbon | CAM/DISP connector | — |

Never power the servo from a GPIO pin. Check the 52Pi PD board's 5 V rating before powering the servo from it.

## Project structure

```
backend/app/services/  agent_service (Qwen agent) · ai_context (grounding, suggestions, prompts) · ai_qwen_service
                       memory_service (object memory: seen / remembered / hidden, containment)
                       detector_service + detector (YOLO/YOLOX/HOG, distance) · camera_service · imu_service
                       dht_service · servo_service · telemetry_service · recording_service · system_monitor
                       mapping_service (localization status, experimental VO) · container
backend/app/api/       api.py (REST) · websocket.py · auth.py · network_guard.py
backend/tests/         pytest suite (demo mode, simulated llama-server)
frontend/src/          console UI: components/console/* (TopBar, StatusCorner, ManualControls, PromptsPanel,
                       VideoFeed (+ servo buttons), DronePanel, MemoryPanel, ChatPanel (Qwen + YOLO, Recordings tab)),
                       components/memory/WorldScene (live 3D memory), React Three Fiber, Recharts
scripts/               install, run, verification, detector model, llama.cpp/Qwen helpers
config/ docs/          systemd units, boot overlay block · setup, hardware, AI, API, judge guide
```

## Testing

```bash
scripts/run_tests.sh      # backend pytest + frontend typecheck, vitest, production build
```

The tests cover:
- agent planning, validation, servo skills, tracking, emergency stop and the offline fallback (simulated llama-server)
- object memory: hide/carry/reveal, out of view vs. occluded, re-identification, the synthetic shell game at 2–4 detector FPS, `goto`
- YOLOv8/YOLOX decoding, NMS, distance geometry and prompts
- sensor parsing, orientation filter, calibration and servo limits
- auth, WebSocket and REST validation
- recording/export, the MJPEG parser and clock sync

## Verified vs. unverified

Built and tested without the physical drone (x86-64 Linux, demo mode, headless browser). Verified:
- the full console
- the agent (keyword planner, plus Qwen protocol against a simulated llama-server)
- tracking in the synthetic scene
- **the YOLOX-S and YOLOX-Tiny models on real photos** (person 0.93 / bus 0.94, person 0.88)
- auth and recording

**Needs validation on the Pi** ([docs/HARDWARE_VERIFICATION.md](docs/HARDWARE_VERIFICATION.md)):
- real sensor/servo/camera I/O
- detector FPS on the Pi 5 CPU
- Qwen speed and plan quality
- the YOLO11n export path
- `scripts/setup_ai.sh` end to end, and Qwen-VL image latency on the Pi 5 CPU
- the servo tilt direction (`STRIX_SERVO_UP_IS_POSITIVE`)
- the object memory with real cups, a real ball and real YOLO on the Pi (verified here only with the synthetic shell game)
- the 52Pi board's servo power capacity

## Documentation

[HUB (multi-drone)](docs/HUB.md) · [JUDGES](docs/JUDGES.md) · [DEVPOST](docs/DEVPOST.md) · [DEMO_VIDEO](docs/DEMO_VIDEO.md) · [PRESENTATION](docs/PRESENTATION_SCRIPT.md) · [SETUP_PI](docs/SETUP_PI.md) · [HARDWARE](docs/HARDWARE.md) · [AI](docs/AI.md) ·
[STREAMING](docs/STREAMING.md) · [API](docs/API.md) · [ARCHITECTURE](docs/ARCHITECTURE.md) ·
[TROUBLESHOOTING](docs/TROUBLESHOOTING.md) · [HARDWARE_VERIFICATION](docs/HARDWARE_VERIFICATION.md)

## License

MIT — see [LICENSE](LICENSE). Optional models have their own licenses: YOLOX-Tiny/YOLOX-S (Apache-2.0), YOLOv8n Open Images (AGPL-3.0),
Ultralytics YOLO11 (AGPL-3.0), Qwen2.5-1.5B (Apache-2.0), Qwen2.5-3B (Qwen Research License — check its terms
for your use). Always confirm on the model card.
