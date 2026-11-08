# STRIX-1 — guide for judges

**One sentence:** STRIX-1 turns a Raspberry Pi 5 on a small drone into an AI camera operator. A
local Qwen model sees through YOLO, reads the drone's motion and environment sensors, **remembers
objects even when they are out of sight or hidden under a cup**, aims the camera itself, estimates
how far away things are and tells the pilot how to move. Everything runs on the drone, with no cloud.

![console](img/dashboard.png)

## What you are looking at (one screen)

| Area | What it shows | Try |
|---|---|---|
| **Status** (top left) | Camera, YOLO, Qwen, MPU6050, DHT11, camera servo (GPIO18 / pin 12), memory and power. A red row says how to fix it. | — |
| **Memory** (left) | Every object STRIX-1 remembers: **seen** now, **remembered** (out of sight, with a timer) or **hidden** under a cup, with an **AIM** button to point the camera at it. | Press AIM on the hidden ball. |
| **Camera servo** (left) | Enable control, Center, −5°/+5° and an angle slider for the SG90 camera tilt servo. | Press Enable control, then +5°. |
| **Live video** (center) | Green boxes: `cup 0.90 · ~0.7 m` = what YOLO sees and about how far. **Dashed orange box** = something remembered behind or under another object (`sports ball #4 · HIDDEN under cup #2 · 12 s`). | Watch the ball go under a cup. |
| **Live 3D** (top right) | **World**: the drone (moving with the MPU6050), a green cone showing where the camera looks, and every remembered object. Hidden objects glow *inside* see-through cups. **Drone**: close-up model with roll/pitch/yaw and the DHT11. | Drag to orbit. |
| **Qwen** (bottom right) | Talk to Qwen. The row above the chat sets what **YOLO looks for**. Qwen also speaks up by itself when something new appears or the ball goes under a cup. **IDENTIFY** asks it to describe the camera image. **Recordings** is the second tab. | See the demo below. |
| **Top bar** | **Start recording** (video + sensors) · **EMERGENCY STOP** (also Space) · **Reset session** | |

## 3-minute demo script

Works on the real drone (three cups and a ball on a table) or in demo mode (`scripts/run_mock.sh`,
clearly marked **SYNTHETIC DEMO DATA**, starts with the cups scene).

**A. Memory: the shell game (90 s)**

1. Watch the video. A cup goes over the ball. Memory and the 3D view switch the ball to **hidden under
   cup #2**, with a timer, and Qwen says so in the chat.
2. The cups are swapped twice. The hidden ball travels with its cup in the 3D view (see-through cup).
3. Ask Qwen **"look up 45 degrees"**: the camera looks away and everything becomes **remembered**, with
   out-of-sight timers counting up.
4. Ask **"Where is the ball?"**. The camera tilts back to the remembered cup and Qwen says which cup it is
   under and which way to turn the drone. When the cup is lifted, Memory logs *"found under cup #2 after
   22 s — memory was right"*.

**B. Camera co-pilot (90 s)**: press **Outdoor** under DEMO in the Memory panel.

1. Ask **"Find the person and keep them centered"**. The servo follows the person, and Qwen tells you
   how far away they are and which way to turn the drone.
2. Press **IDENTIFY**: Qwen looks at the camera image and describes it (needs the vision model).
3. Ask **"How warm is it?"**: the DHT11 reading.
4. Press **Space (EMERGENCY STOP)** during a task. The task stops, the servo turns off and stays off
   until you press **Enable control** again.

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
- **Safety by design.** The AI can move *only* the camera tilt servo, within safe limits and a slew rate,
  and only for a task the operator gave it. A one-key emergency stop (Space) turns it off and keeps it off
  until the operator turns it back on.
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
