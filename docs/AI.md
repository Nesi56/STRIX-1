# AI: the Qwen camera agent

STRIX-1 runs **one** AI model: a small **Qwen** model running locally on the Raspberry Pi 5 through
llama.cpp. No cloud service is used. Qwen:

1. **sees**: with the Qwen-VL vision model it looks at the current camera frame and identifies what is there (`[SEEN]`),
2. **reacts to YOLO**: in WATCH mode it comments by itself when YOLO detects new objects,
3. **controls the camera tilt servo** (look up/down, center, scan, find an object, keep it centered),
4. **reads the sensors**: MPU6050 attitude, DHT11 temperature/humidity, and YOLO detections,
5. **estimates distance and direction** to detected objects,
6. **suggests how to move** or how to complete the task. The human pilot flies the drone;
   STRIX-1 never sends flight commands.

## The agent graph

The dashboard's **AGENT GRAPH** tab lights up each stage as it runs:

```
GENERATOR ──plan──▶ EXECUTION LOOP [ OBSERVER → PILOT → UNTIL ↺ ] ──complete──▶ REPORT
```

| Stage | What happens | Code |
|---|---|---|
| **GENERATOR** | Qwen turns the operator's task into a plan of at most 5 camera *skills*. Output is constrained by a JSON schema (llama-server `response_format` → grammar), so even a 1.5–3 B model always returns valid, parseable JSON. The plan is then validated: unknown skills or classes are dropped and angles are clamped to the servo's safe range. | `agent_service._plan`, `ai_context.PLAN_SCHEMA` |
| **OBSERVER** | Snapshot of IMU, DHT11, servo state and the newest YOLO detections (with range/bearing). | `ai_context.observe` |
| **PILOT** | Starts the skill on the camera servo. All motion goes through `ServoService`, which enforces the safe limits, the slew rate and the operator's enable switch. | `agent_service._skill` |
| **UNTIL** | Polls the skill's completion condition at the detector rate: *reached*, *found*, *centered* or timeout. | `agent_service._skill` |
| **REPORT** | Qwen writes the answer from the final observation, the action log and **deterministic** movement suggestions. Tags: `[MEASURED]`, `[DETECTED]`, `SUGGEST:`, `[INTERPRETATION]`, `[UNAVAILABLE]`. | `ai_context.REPORT_PROMPT` |

### Skills (the only things the agent can do)

| Skill | Args | Completes when |
|---|---|---|
| `look` | `tilt` (° from level, + up) | servo reached the target |
| `tilt` | `delta` (°) | servo reached the target |
| `center` | — | camera level |
| `scan` | — | swept 5 tilt angles, then returns to the best view |
| `find` | `target` (COCO class) | target detected (or not found in range) |
| `track` | `target`, `seconds` | time elapsed. A proportional controller keeps the target vertically centered. Fails if the target is lost for > 3 s. |
| `wait` | `seconds` | time elapsed |

The servo only tilts. Horizontal alignment is given as a suggestion ("yaw right ~14°").

### Distance and movement suggestions

- **Distance** comes from the object's apparent size with a pinhole camera model:
  `distance ≈ f · H / h`, where `f = (W/2)/tan(HFOV/2)` (HFOV 102° for the Camera Module 3 Wide), `H` is
  a typical object height (person 1.7 m, car 1.5 m, chair 0.9 m …) and `h` is the box height in
  pixels. It's an **estimate** (±30 % or worse): a sitting person or a box cut off by the frame
  edge breaks the assumption. Cut-off boxes are reported as an upper bound (`≤ X m`).
- **Bearing** comes from the box's horizontal position (`atan((x − W/2)/f)`).
- **Suggestions** are computed in code from bearing, range and attitude, not invented by the model:
  - "Yaw right ~15° to bring the person to the center of the frame."
  - "Advance ~3.9 m to reach a 1.5 m standoff (estimated range 5.4 m, ±30 %)."
  - "Level the airframe first (roll +24°)." / "Power problem detected — land …"

  The model rephrases them; the UI also shows the raw list.
- There is no GPS, so absolute position is "unavailable — no validated localization source".

### Safety rules

- The only actuator is the camera tilt servo. Motion requires the operator to **enable servo
  control** (E key or the button). With control disabled the agent still observes and advises,
  and reports that it couldn't move the camera.
- **EMERGENCY STOP** (button or Space) disables servo control and cancels the agent immediately,
  including an in-flight Qwen request. **Esc** stops just the agent.
- Model output is never executed; shell/code blocks are stripped from answers.
- Everything runs on the Pi; no data leaves the local network.

### When Qwen is offline

If llama-server is loading or unreachable, the dashboard keeps working. A **keyword planner**
(e.g. "look up 20 degrees", "find the person", "track the dog", "scan") and a **rule-based report**
stand in, clearly labeled *RULE PLAN* / *rule-based*. If Qwen returns unusable JSON, the same
fallback is used for that task.

## Vision: Qwen looks at the camera

When the loaded model reports vision support (llama-server `/props` → `modalities.vision`), STRIX-1 attaches
the newest camera frame (scaled to 448 px wide, `STRIX_AGENT_IMAGE_PX`) to the REPORT for visual requests
("identify this", "what do you see", "describe", "how many…", "what colour…") and for `scan`/`find` tasks.
Qwen marks what it identifies in the image as `[SEEN]`, kept separate from YOLO's `[DETECTED]` boxes. The chat
shows **SAW CAMERA IMAGE** on those answers. A text-only model never receives images; it says
`[UNAVAILABLE]` for visual identification instead.

## WATCH mode: Qwen responds to YOLO

With **WATCH ON** (chat header, default on), a background loop checks the YOLO detections every second.
When the set of prompted objects changes and stays stable for ~2 s, Qwen writes a 1–2 sentence comment
(with the camera image if it has vision): what appeared, estimated distance/direction, and a `SUGGEST:`
line. Comments are at least `STRIX_AGENT_WATCH_INTERVAL_S` (20 s) apart, never run during an operator
task, and are cancelled the moment you give Qwen a task. Without Qwen, a rule-based announcement is used.

## Running Qwen on the Pi 5 — one command

```bash
cd ~/STRIX-1 && scripts/setup_ai.sh          # Qwen2.5-VL-3B-Instruct (best quality)
cd ~/STRIX-1 && scripts/setup_ai.sh --fast   # Qwen2-VL-2B-Instruct (faster)
```

The script:
1. installs the YOLO detector
2. builds llama.cpp
3. asks Hugging Face which GGUF files `ggml-org/Qwen2.5-VL-3B-Instruct-GGUF` contains, then downloads the Q4_K_M model and its vision projector (mmproj)
4. installs the `strix1-llama` service (localhost only)
5. writes the `QWEN_*` settings into `.env`
6. restarts STRIX-1 and waits until Qwen answers

It is safe to re-run. Run it inside `tmux`; it downloads about 3–4 GB and the build takes 10–20 minutes.

**Speed (estimate, measure on your Pi):** a text answer takes seconds to tens of seconds. An answer
with a camera image adds image encoding on the CPU, so expect roughly 20–60 s with the 3B model and less
with `--fast`. Servo skills such as `track` don't wait for the model.

| Setting | Default | Meaning |
|---|---|---|
| `QWEN_SERVER_URL` | `http://127.0.0.1:8080` | llama-server address (keep it on localhost) |
| `QWEN_MODEL_ID` | set by setup_ai.sh | label shown in the UI |
| `QWEN_CTX_SIZE` / `QWEN_THREADS` | 4096 / 3 | context length / CPU threads (one core left for camera and sensors) |
| `STRIX_AGENT_WATCH` / `STRIX_AGENT_WATCH_INTERVAL_S` | true / 20 | YOLO watch comments |
| `STRIX_AGENT_IMAGE_PX` | 448 | width of frames sent to the vision model |

Text-only models (e.g. `Qwen/Qwen2.5-1.5B-Instruct-GGUF` via `scripts/download_qwen.sh`) also work. They
plan and report, but can't identify things in the image.

## Object detection ("YOLO stream")

| Engine | When | Notes |
|---|---|---|
| ONNX **YOLOX-S** | default from `scripts/get_detector_model.sh` | OpenCV Zoo, Apache-2.0, COCO-80; verified in development (person 0.93 on a test photo). Expect a few FPS on the Pi 5. |
| ONNX **YOLO11n / YOLOv8** | `scripts/get_detector_model.sh --yolo11n` | ~4× lighter, so faster; exported with Ultralytics (AGPL-3.0). Its decoder is unit-tested against the documented output format, but no exported model was run in development. |
| OpenCV **HOG** | no model installed | person only, fallback |
| **Synthetic** | demo mode | ground-truth boxes from the synthetic camera, labeled SYNTHETIC |

Inference uses onnxruntime (2 threads) when installed, otherwise OpenCV DNN.
**Prompts** (left panel) choose which classes are drawn on the video; the agent can still see
every class. Only COCO-80 class names are accepted (e.g. `person, car, dog, chair, bottle`). This is
a closed-vocabulary detector, not open-vocabulary.

## Example tasks

- "Find the person and keep them centered" → `find` + `track`; the report gives distance, yaw and advance suggestions.
- "How far away is the person and how should I move?" → no camera motion; range + suggestions.
- "Scan the area and tell me what you see" → `scan` sweep and summary.
- "Look up 15 degrees" / "center the camera".
- "Is the IMU behaving normally?" → uses the deterministic IMU health checks.
- "What are the temperature and humidity?"
