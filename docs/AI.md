# AI: the Qwen camera agent

STRIX-1 runs **one** AI model: a small **Qwen** model running locally on the Raspberry Pi 5 through
llama.cpp. No cloud service is used. Qwen:

1. **sees**: with the Qwen-VL vision model it looks at the current camera frame and identifies what is there,
2. **reacts to YOLO**: it comments by itself when YOLO detects new objects or an object goes under a cup,
3. **controls the camera tilt servo** (look up/down, center, scan, find an object, keep it centered,
   go back to a remembered object),
4. **reads the sensors**: MPU6050 attitude, DHT11 temperature/humidity, and YOLO detections,
5. **estimates distance and direction** to detected objects,
6. **suggests how to move** or how to complete the task. The human pilot flies the drone;
   STRIX-1 never sends flight commands.
7. **remembers objects** it has seen, including ones hidden under a cup (object permanence). See
   [Object memory](#object-memory-seen-vs-remembered-vs-hidden).

## The agent graph

Each request runs through these stages (the chat shows the camera steps while they run):

```
GENERATOR ──plan──▶ EXECUTION LOOP [ OBSERVER → PILOT → UNTIL ↺ ] ──complete──▶ REPORT
```

| Stage | What happens | Code |
|---|---|---|
| **GENERATOR** | Qwen turns the operator's task into a plan of at most 5 camera *skills*. Output is constrained by a JSON schema (llama-server `response_format` → grammar), so even a 1.5–3 B model always returns valid, parseable JSON. The plan is then validated: unknown skills or classes are dropped and angles are clamped to the servo's safe range. | `agent_service._plan`, `ai_context.PLAN_SCHEMA` |
| **OBSERVER** | Snapshot of IMU, DHT11, servo state and the newest YOLO detections (with range/bearing). | `ai_context.observe` |
| **PILOT** | Starts the skill on the camera servo. All motion goes through `ServoService`, which enforces the safe limits, the slew rate and the operator's enable switch. | `agent_service._skill` |
| **UNTIL** | Polls the skill's completion condition at the detector rate: *reached*, *found*, *centered* or timeout. | `agent_service._skill` |
| **REPORT** | Qwen answers in 1–4 short plain sentences from the final observation, the action log and **deterministic** movement suggestions ("Turn the drone right about 15°"). Distances are said as "about". | `ai_context.REPORT_PROMPT` |

### Skills (the only things the agent can do)

| Skill | Args | Completes when |
|---|---|---|
| `look` | `tilt` (° from level, + up) | servo reached the target |
| `tilt` | `delta` (°) | servo reached the target |
| `center` | — | camera level |
| `scan` | — | swept 5 tilt angles, then returns to the best view |
| `find` | `target` (COCO class) | target detected (or not found in range) |
| `track` | `target`, `seconds` | time elapsed. A proportional controller keeps the target vertically centered. Fails if the target is lost for > 3 s. |
| `goto` | `target` (class or `#id`) | the camera is aimed at the **remembered** object (even out of view or hidden under a cup). The report says whether it is SEEN again, plus yaw/distance advice. |
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

- The only actuator is the camera tilt servo. It moves only for something the operator asked for
  (a task for Qwen, AIM, the tilt buttons/keys), which turns servo control on. Qwen's automatic comments
  never move it.
- **EMERGENCY STOP** (button or Space) turns servo control off and cancels the agent immediately,
  including an in-flight Qwen request. It **latches**: tasks and buttons can't move the camera until the
  operator presses **Enable control** in the Camera Servo panel (or E). **Esc** stops just the agent.
- Model output is never executed; shell/code blocks are stripped from answers.
- Everything runs on the Pi; no data leaves the local network.

### When Qwen is offline

If llama-server is loading or unreachable, the dashboard keeps working. A **keyword planner**
(e.g. "look up 20 degrees", "find the person", "track the dog", "scan") and a **rule-based report**
stand in, clearly labeled *RULE PLAN* / *rule-based*. If Qwen returns unusable JSON, the same
fallback is used for that task.

## Object memory: seen vs. remembered vs. hidden

STRIX-1 keeps a memory of every object of the prompt classes (plus cups, bowls, vases and wine glasses) that
YOLO has seen. Each object is stored as a **direction from the drone** (azimuth/elevation from relative yaw,
pitch, roll and the camera tilt) plus an estimated range, so it stays put in memory when the camera looks
elsewhere.

| State | Meaning | Shown as |
|---|---|---|
| **SEEN** | detected in the current frame | solid green in 3D, green box on the video |
| **REMEMBERED** | out of view (the camera looks elsewhere) or not detected where expected, with a live *out of sight* timer | grey ghost in 3D, dashed grey box on the video when its spot is in view |
| **HIDDEN** | disappeared where a container (cup, bowl …) covers it. It now **moves with that container** | orange, glowing *inside* the see-through cup in 3D; dashed orange box on the video |

When the container is lifted and the object is there, the memory logs *"revealed after 22 s hidden under
cup #2: memory was right"*. If it turns up somewhere else, it logs *"memory corrected"*. Qwen sees the
memory in its observation (`MEMORY:` lines) and announces hide, reveal and relocation events in the chat by itself.

**Send it to a remembered target.** Ask *"where is the ball?"*, *"go to the remembered target"* or *"aim at
#4"*, or press **AIM** in the Memory panel. The `goto` skill tilts the camera to the remembered elevation and
returns pilot advice ("Yaw left ~14° to face sports ball #4. It is ~0.7 m away"). It never moves the aircraft.

Limits (shown in the UI): the drone is assumed to hold its position, relative yaw drifts until the IMU is
calibrated, identical cups keep their identity only through continuous tracking (≥ 2 detections/s), and
ranges are size-prior estimates (±30 %). Set `STRIX_DETECTOR_SIZE_PRIORS="sports ball=0.067"` for a tennis
ball so its distance is right.

**Demo:** in demo mode (`scripts/run_mock.sh`) the synthetic camera plays a 48-second *shell game*:
1. a cup covers the ball;
2. the cups are swapped twice;
3. the camera can look away;
4. the cup is lifted to check the memory.

Switch scenes with the DEMO SCENE buttons in the MEMORY tab.

### Why not Qwen-3D on the Pi?

[Qwen-3D](https://huggingface.co/katefgroup/Qwen-3D) ([code](https://github.com/ll220/qwen3d)) is a
3-billion-parameter 3D vision-language model. It needs a CUDA GPU, detectron2/pytorch3d, posed RGB-D input
and PyTorch `.pth` weights, and there is no GGUF/llama.cpp build. It can't run on a Raspberry Pi 5 CPU, and
the drone has no depth camera.

STRIX-1 takes the same idea in a form that fits on the Pi:
- a persistent, spatially grounded memory built from YOLO + IMU + servo;
- the CaP-X-style "hidden object under cups" task ([CaP-X](https://arxiv.org/abs/2603.22435));
- a live 3D view of what is seen versus remembered;
- Qwen2.5-VL reasoning over that memory.

## Vision: Qwen looks at the camera

When the loaded model reports vision support (llama-server `/props` → `modalities.vision`), STRIX-1 attaches
the newest camera frame (scaled to 448 px wide, `STRIX_AGENT_IMAGE_PX`) to the REPORT for visual requests
("identify this", "what do you see", "describe", "how many…", "what colour…") and for `scan`/`find` tasks.
The chat shows a small image icon on those answers. A text-only model never receives images; it says it can
only name the YOLO objects instead.

## Qwen speaks up by itself

A background loop checks the YOLO detections and the object memory every second. When the set of objects
changes and stays stable for ~2 s, or an object goes under / comes out from a cup, Qwen writes one short
sentence (with the camera image if it has vision). Object comments are at least
`STRIX_AGENT_WATCH_INTERVAL_S` (20 s) apart; memory events at least 5 s. They never run during an operator
task. Without Qwen, a rule-based sentence is used. Turn it off with `STRIX_AGENT_WATCH=false`.

## Running Qwen on the Pi 5 — one command, then it starts by itself

```bash
cd ~/STRIX-1 && scripts/setup_ai.sh            # Qwen2-VL-2B-Instruct (default: fast, keeps the Pi cooler)
cd ~/STRIX-1 && scripts/setup_ai.sh --best     # Qwen2.5-VL-3B-Instruct (better answers, slower and hotter)
cd ~/STRIX-1 && scripts/setup_ai.sh --everyday # also install the 600-object detector (pens, mugs …) without asking
```

The script:
1. installs the YOLO detector
2. builds llama.cpp
3. asks Hugging Face which GGUF files `ggml-org/Qwen2-VL-2B-Instruct-GGUF` (or the 3B with `--best`) contains, then downloads the Q4_K_M model and its vision projector (mmproj), and writes their paths to `.env`
4. installs the `strix1-llama` service (localhost only)
5. writes the `QWEN_*` settings into `.env`
6. restarts STRIX-1 and waits until Qwen answers

After that Qwen starts automatically on every boot (the `strix1-llama` service). As a backup, the
STRIX-1 backend starts llama-server itself when no Qwen server answers within ~20 s and a model is in
`models/` (`QWEN_MANAGE_SERVER=auto`, the default). Paths are auto-detected:
`~/llama.cpp/build/bin/llama-server` and the `.gguf` files in `models/`.

It is safe to re-run. Run it inside `tmux`; it downloads about 3–4 GB and the build takes 10–20 minutes.

**Keeping it cool.** The defaults are chosen for heat:
- 720p camera stream;
- YOLOX-Tiny at 3 detections/s, slowed to 2/s while Qwen is thinking and to 1–1.5/s above 75 °C;
- the 2B Qwen model;
- 336 px images for vision answers;
- automatic comments that are text-only, at most every 45 s, and skipped above 70 °C.

The STATUS corner shows the CPU temperature. On a drone or in a case, fit the official Raspberry Pi **Active Cooler**: the Pi 5 throttles at 85 °C.

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
| ONNX **YOLOX-Tiny** (416 px) | default from `scripts/get_detector_model.sh` | Megvii YOLOX release, Apache-2.0, SHA-256 checked, COCO-80 (phones, cups, bottles, laptops, books, scissors …). About 4× less compute than YOLOX-S; verified in development (bus 0.94, person 0.88). |
| ONNX **YOLOv8n Open Images V7** (416 px) | `scripts/get_detector_model.sh --everyday` (or answer *yes* in `setup_ai.sh`) | 600 everyday objects incl. **pen**, mobile phone, mug, coffee cup. Exported on the Pi with Ultralytics (AGPL-3.0, temporary CPU PyTorch). Names are mapped to STRIX-1's (mobile phone → cell phone, mug → cup …). Small/thin objects like pens are hard for a nano model, so expect misses. Not run in development (export blocked here), only its decoding and label mapping are unit-tested. |
| ONNX **YOLOX-S** | `--yolox-s` | more accurate, ~4× more CPU, runs the Pi hotter |
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
- "Where is the ball?" / "Send it to the remembered target" / "What do you remember?" → `goto` and an answer from memory.
