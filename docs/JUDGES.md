# STRIX-1 — guide for judges

**One sentence:** STRIX-1 turns a Raspberry Pi 5 on a small drone into an AI camera operator. A
local Qwen model sees through YOLO, reads the drone's motion and environment sensors, **remembers
objects even when they are out of sight or hidden under a cup**, aims the camera itself, estimates
how far away things are and tells the pilot how to move. Everything runs on the drone, with no cloud.

![console](img/dashboard.png)

## What you are looking at (one screen)

| Area | What it shows | Try |
|---|---|---|
| **System status** (top-left corner) | Camera, YOLO, Qwen, MPU6050, DHT11, servo and battery/power. Red rows show the reason. | — |
| **Manual controls** | Keyboard control of the camera tilt servo, speed and step sliders, current tilt. | **E** enable · **W/↑** up · **S/↓** down · **C** center |
| **YOLO prompts** | Which object classes are drawn on the video. | Type `car` and press Enter. |
| **Live video** (center) | Camera feed with green boxes: `cup 0.90 · ~0.7 m` = class, confidence, estimated distance. **Dashed orange box** = an object it remembers *behind/under* something (`sports ball #4 · HIDDEN under cup #2 · 12 s`). | Watch the ball disappear and the dashed box stay. |
| **Live 3D · Memory world** (below the video) | The drone (moving with the MPU6050) with a green cone showing where the camera looks, and every object it remembers: solid = SEEN, ghost = REMEMBERED, **glowing inside a see-through cup = HIDDEN**, each with how long it has been out of sight. **Drone IMU** shows the close-up quad model, attitude and DHT11. | Drag to orbit. |
| **Memory / Agent graph / Radar / Recordings** (top-right tabs) | MEMORY: seen / remembered / hidden list with live timers, **AIM** buttons and memory events · Qwen's pipeline stage by stage · top-down radar · saved recordings with video, CSV and JSON. | Press AIM on the hidden ball. |
| **Chat** | Talk to Qwen. **WATCH ON**: Qwen comments by itself when YOLO sees something new. **IDENTIFY**: Qwen looks at the camera image. | See the demo script below. |
| **Top bar** | **Start recording** (video + sensors) · **EMERGENCY STOP** (also Space) · **RESET SESSION** | |

## 3-minute demo script

Works on the real drone (three cups and a ball on a table) or in demo mode (`scripts/run_mock.sh`,
clearly marked **SYNTHETIC DEMO DATA**, starts with the shell-game scene).

**A. Memory: the shell game (90 s)**

1. Watch the video. A cup is placed over the ball. The MEMORY tab and the 3D view switch the ball to
   **HIDDEN under cup #2**, with a timer, and Qwen says so in the chat.
2. The cups are swapped twice. The hidden ball travels with its cup in the 3D view (see-through cup).
3. Press **E** (enable the servo), then **W** about 8 times: the camera looks at the ceiling. Everything
   turns **REMEMBERED**, with out-of-sight timers counting up.
4. Ask Qwen: **"Where is the ball?"** → `goto sports ball`. The camera tilts back down to the remembered
   cup and the report says where it is and how to yaw. When the cup is lifted: *"revealed after 22 s
   hidden under cup #2: memory was right"*.

**B. Camera agent (90 s)** — press **Outdoor** under DEMO SCENE in the MEMORY tab.

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

- **Object permanence on a Pi.** A spatial memory built from YOLO + IMU + servo pose keeps objects in a
  drone-centred frame. It tells *seen* from *remembered* (out of view, occluded) and *hidden* (inside a
  container that it then follows). It re-identifies objects and corrects itself when it was wrong. Tested
  on a synthetic shell game at 2–10 detector FPS. (A 3D VLM such as Qwen-3D needs a GPU and a depth camera,
  so this is the edge-sized version; see [AI.md](AI.md#why-not-qwen-3d-on-the-pi).)
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
