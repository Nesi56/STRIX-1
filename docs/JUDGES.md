# STRIX-1 — guide for judges

**One sentence:** STRIX-1 turns a Raspberry Pi 5 on a small drone into an AI camera operator. A
local Qwen model sees through YOLO, reads the drone's motion and environment sensors, aims the
camera itself, estimates how far away things are and tells the pilot how to move. Everything runs
on the drone, with no cloud.

![console](img/dashboard.png)

## What you are looking at (one screen)

| Area | What it shows | Try |
|---|---|---|
| **System status** (top-left corner) | Camera, YOLO, Qwen, MPU6050, DHT11, servo and battery/power. Red rows show the reason. | — |
| **Manual controls** | Keyboard control of the camera tilt servo, speed and step sliders, current tilt. | **E** enable · **W/↑** up · **S/↓** down · **C** center |
| **YOLO prompts** | Which object classes are drawn on the video. | Type `car` and press Enter. |
| **Live video** (center) | Camera feed with green boxes: `person 0.88 · ~5.4 m` = class, confidence, estimated distance. | Watch the boxes follow the people. |
| **Drone · IMU simulation** (below the video) | 3D model of the 250 mm quad moving with the MPU6050, artificial horizon, roll/pitch/yaw, DHT11. | Drag to orbit; tilt the real drone. |
| **Agent graph / Radar / Recordings** (top-right tabs) | Qwen's pipeline lighting up stage by stage · a top-down radar of objects by distance and bearing · saved recordings with video, CSV and JSON. | |
| **Chat** | Talk to Qwen. **WATCH ON**: Qwen comments by itself when YOLO sees something new. **IDENTIFY**: Qwen looks at the camera image. | See the demo script below. |
| **Top bar** | **Start recording** (video + sensors) · **EMERGENCY STOP** (also Space) · **RESET SESSION** | |

## 3-minute demo script

Works on the real drone or in demo mode (`scripts/run_mock.sh`, clearly marked
**SYNTHETIC DEMO DATA**).

1. **Enable the camera servo**: press **E** (the panel shows *ARMED*). Press **W** twice: the
   camera tilts up 10°, and in demo mode the view and the 3D model follow.
2. **Ask: "Find the person and keep them centered".**
   - GENERATOR → Qwen returns a JSON plan: `find person`, then `track person 8 s`.
   - PILOT/UNTIL → the servo moves until the person is vertically centered (the report states
     the % of frames centered).
   - REPORT → e.g. "[DETECTED] person 0.92, ~5.4 m, 15° right … SUGGEST: Yaw right ~15° … Advance
     ~3.9 m to reach a 1.5 m standoff".
3. **Press IDENTIFY**: Qwen looks at the camera image and describes it (`[SEEN]`), beyond YOLO's labels.
4. **Open RADAR**: the same person appears at ~5 m, 15° right.
5. **Ask: "Scan the area and tell me what you see"**: the camera sweeps its tilt range and lists
   what it saw at each angle.
6. **Press Space (EMERGENCY STOP)** during a task: the run stops immediately and servo control
   is disabled.

## Why it is technically interesting

- **Small model, reliable control.** Qwen's planning is constrained by a JSON schema enforced as a
  grammar in llama.cpp, so the 1.5–3 B model can't produce malformed commands. Fast skills
  (tracking at detector rate) keep motion responsive while the LLM only plans and explains.
- **Honest numbers.** Distances use a stated pinhole model with object-size priors and are always
  labeled as estimates. Suggestions come from geometry, not model guesses. Yaw is shown as
  *relative* because the MPU6050 has no compass. Battery and GPS position are shown as
  unavailable rather than faked. Demo data is labeled SYNTHETIC on every screen.
- **Safety by design.** The AI can move *only* the camera tilt servo, only after the operator
  enables it, within safe limits and a slew rate, and a one-key software emergency stop (Space) disables it instantly.
  Aircraft motion is advice for the human pilot.
- **Edge-only.** Camera, YOLO, sensor fusion and Qwen all run on a Raspberry Pi 5 (16 GB) and are
  viewed from any laptop or phone browser over Wi-Fi.
- **Engineering.** FastAPI backend with one thread per device, WebSocket telemetry at 20 Hz,
  low-latency MJPEG with measured glass-to-browser latency, SQLite recording with playback and
  CSV/JSON export, and 100+ automated tests.

## Hardware

Raspberry Pi 5 (16 GB) · Camera Module 3 Wide on a TowerPro SG90 tilt servo (GPIO18) · MPU6050
IMU (I2C) · DHT11 (GPIO4, kernel driver) · SpeedyFPV ZMR250 frame · USB-C PD power bank.
Wiring: [HARDWARE.md](HARDWARE.md).

## Run it yourself (no hardware needed)

```bash
git clone https://github.com/Nesi56/STRIX-1.git && cd STRIX-1
(cd frontend && npm ci && npm run build)
scripts/run_mock.sh          # prints a login token → open http://localhost:8000
```

Without a Qwen server running, the agent uses its labeled keyword planner, so the whole demo
still works. To include Qwen, see [AI.md](AI.md).
