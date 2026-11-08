# STRIX-1 — Devpost submission (paste-ready)

Each section below matches a field on the Devpost submission form. Copy the text under a heading into
the field with the same name. Devpost's "About the project" box accepts Markdown, so the headings,
lists and tables paste as they are.

Before you submit, check that every claim matches what you actually showed on the real drone. The
servo, MPU6050 and DHT11 code is tested in demo mode and in automated tests. Wiring and behaviour on
your airframe are yours to confirm (see [HARDWARE_VERIFICATION.md](HARDWARE_VERIFICATION.md)).

## Project name

STRIX-1

## Elevator pitch

A Raspberry Pi 5 drone co-pilot: local Qwen and YOLO see, a servo aims the camera, the IMU and
temperature sensor ground every answer, and it remembers objects even when they're hidden under a cup.

## Inspiration

Small FPV drones carry a camera, but the pilot has to do all the looking. We wanted the drone itself to
help: notice what's in view, say how far away it is and which way to turn, aim its own camera, and keep
track of things after they leave the picture. Cloud AI was not an option on a drone, so everything had
to run on the Raspberry Pi 5 that rides on the frame.

We were also inspired by robotics research on object permanence, like the "find the object hidden under
the cups" task from the CaP-X benchmark. A 3D vision-language model such as Qwen-3D needs a GPU and a
depth camera, so we built the edge-sized version: a spatial memory made from YOLO, the IMU and the camera
servo, with a small Qwen model reasoning over it.

## What it does

STRIX-1 turns a Raspberry Pi 5 on a 250 mm quadcopter into an AI co-pilot that you open in any browser
on the same Wi-Fi.

**It sees.** The Camera Module 3 Wide streams live video. YOLO draws boxes with an estimated distance,
for example `cup 0.90 · ~0.7 m`. Ask "what do you see?" and the Qwen vision model looks at the frame
itself, so it isn't limited to YOLO's classes.

**It aims the camera (servo).** The camera sits on an SG90 tilt servo on GPIO18. Ask Qwen "look up",
"find the person and keep them centered" or "where is the ball?" and it plans the camera moves and runs
them: look, tilt, scan, find, track and go to a remembered object. You can also drive it from the
Camera Servo panel (Enable control, ±5°, slider) or the keyboard. Every move stays inside safe limits
(30–150°) and a 60°/s slew rate, and EMERGENCY STOP (Space) turns the servo off until you enable it
again.

**It feels the drone (MPU6050 IMU).** It reads the IMU at 100 Hz and fuses it into roll, pitch and
relative yaw with a complementary filter. A live 3D model of the drone moves with it. The same attitude
places objects in the memory, and Qwen uses it to say things like "level the drone first".

**It knows the air (DHT11).** It reads temperature and humidity through the Pi 5 kernel driver, with
session min/max. Ask "how warm is it?" and Qwen answers from the sensor. A heat guard also watches the
Pi's own CPU temperature and slows the AI down before the Pi throttles.

**It remembers.** Every object is kept as **seen**, **remembered** (out of view, with a timer) or
**hidden** (under a cup), in a frame centred on the drone. When cups are shuffled, the hidden ball moves
with its cup. A live 3D view shows the hidden ball glowing inside a see-through cup, and the video shows
a dashed box where it should be. When the cup is lifted, it logs "found under cup #2 after 22 s — memory
was right", or corrects itself.

**It advises the pilot.** Answers are short, plain sentences: how far, which way to turn, how far to move
closer. STRIX-1 never sends flight commands. The pilot flies; STRIX-1 only moves its camera.

**It records.** One button records the camera video and every sensor together, with playback and
CSV/JSON export.

## How we built it

| Part | What we used |
|---|---|
| Brain | Raspberry Pi 5 (16 GB), everything on-board, no cloud |
| Camera | Camera Module 3 Wide through Picamera2, low-latency MJPEG to the browser |
| Camera servo | TowerPro SG90 on GPIO18 (pin 12), gpiozero with lgpio, 50 Hz trajectory loop with slew limiting, powered from the 52Pi PD board's 5 V |
| IMU | MPU6050 on I2C (pins 3/5), 14-byte burst reads at 100 Hz, complementary filter, calibration |
| Temperature/humidity | DHT11 on GPIO4 (pin 7) through the kernel `dht11` overlay (IIO), read every 3 s |
| Object detection | YOLOX-Tiny (ONNX Runtime), with an optional 600-object Open Images model that knows pens, phones and mugs |
| Language + vision | Qwen2-VL-2B (or Qwen2.5-VL-3B) on llama.cpp, schema-constrained JSON plans so a small model always returns valid commands |
| Backend | Python, FastAPI, one thread per device, WebSocket telemetry at 20 Hz, SQLite recording |
| Frontend | React, TypeScript, Vite, Tailwind, React Three Fiber for the 3D drone and memory world |

How a request flows: Qwen turns "find the person and keep them centered" into a short plan of camera
skills. Each skill drives the servo and checks its goal at the detector's rate. Qwen then answers from
the IMU, DHT11, YOLO and memory data, plus movement suggestions computed from geometry rather than
guessed by the model. If Qwen is offline, a keyword planner and rule-based answers take over, so the
drone never goes silent.

Distances come from a pinhole camera model: `distance ≈ f · H / h`, with the Camera Module 3 Wide's
102° field of view and typical object heights. They are always labelled as estimates.

## Challenges we ran into

- **Heat.** Running a camera stream, YOLO and an LLM on a fanless Pi 5 got hot fast. We moved to a 720p
  stream, a 4× lighter detector and a 2B model, made automatic comments text-only, and added a heat guard
  that slows YOLO above 75 °C.
- **GPIO on the Pi 5.** The Pi 5's new RP1 chip changed how GPIO works. The servo goes through gpiozero
  and lgpio, and we learned that lgpio needs a writable working directory when it runs as a hardened
  systemd service.
- **DHT11 timing.** Bit-banging the DHT11 from Python is unreliable, so we used the kernel's own driver.
- **Yaw drift.** The MPU6050 has no compass, so yaw drifts. We show it as relative yaw, offer
  calibration, and let the memory correct itself when an object turns up somewhere else.
- **Identical cups.** Keeping track of which cup hides the ball meant motion prediction between
  detector frames. It stays correct at 2 detections per second and up.

## Accomplishments that we're proud of

- A small model controls the camera reliably. Its plans are checked against a JSON schema and clamped to
  the servo's safe range.
- Object permanence on a Raspberry Pi: the memory follows the right cup through two swaps and a
  look-away, in tests at 2–10 detector frames per second.
- Everything is honest: distances are labelled as estimates, there is no fake GPS or battery %, and demo
  data is labelled SYNTHETIC on every screen.
- One screen with live video, 3D, memory, sensors and chat, and 160+ automated tests.

## What we learned

- On the edge, the model is only half the work. Grounding it in real sensors makes a small model useful.
- Schema-constrained output turns a 2–3 B model into a dependable controller.
- Thermal budget is a design constraint, just like latency.
- Saying "I don't know" (unavailable data, estimates) builds more trust than a confident guess.

## What's next for STRIX-1

- Fly it: validate the servo, IMU and DHT11 on the airframe in flight and tune the filter.
- A second servo axis (pan) so the camera can turn without yawing the drone.
- A depth estimate or a small depth sensor for better distances and a true 3D memory.
- Faster vision with a Pi AI accelerator.

## Built with

`raspberry-pi` `python` `fastapi` `react` `typescript` `vite` `tailwindcss` `three.js` `react-three-fiber`
`onnxruntime` `opencv` `yolox` `llama.cpp` `qwen` `picamera2` `gpiozero` `lgpio` `sqlite` `websockets`
`mpu6050` `dht11` `sg90-servo`

## Try it out

- Code: https://github.com/Nesi56/STRIX-1
- Run the demo without hardware: `scripts/run_mock.sh` (see docs/JUDGES.md)

## Image gallery (suggested)

1. The console during the shell game: `docs/img/dashboard.png`
2. A photo of the drone showing the camera on its SG90 tilt servo
3. A close-up of the wiring: MPU6050 on I2C, DHT11 on GPIO4, servo on GPIO18
4. The live 3D view with the hidden ball inside a see-through cup

## Demo video (2 minutes, suggested shots)

1. **0:00** The drone on the table; the console on a laptop. One sentence on what STRIX-1 is.
2. **0:15** Tilt the drone by hand: the 3D model follows the IMU. Breathe on the DHT11: humidity rises.
3. **0:35** Press Enable control and use ±5°: the camera servo moves. Ask "look up a bit".
4. **0:55** Shell game: the ball goes under a cup and the memory marks it hidden. Shuffle the cups.
5. **1:20** Ask "where is the ball?": the camera aims at the right cup and Qwen says which way to turn.
6. **1:40** Lift the cup: "memory was right". Press Space for EMERGENCY STOP.
