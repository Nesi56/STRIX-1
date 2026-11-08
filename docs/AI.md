# AI: the Qwen camera agent

STRIX-1 runs **one** AI model: a small **Qwen** model running locally on the Raspberry Pi 5 through
llama.cpp. No cloud service is used. Qwen:

1. **controls the camera tilt servo** (look up/down, center, scan, find an object, keep it centered),
2. **reads the sensors**: MPU6050 attitude, DHT11 temperature/humidity, and YOLO detections,
3. **estimates distance and direction** to detected objects,
4. **suggests how to move** or how to complete the task. The human pilot flies the drone;
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

## Running Qwen on the Pi 5

| Setting | Default | Meaning |
|---|---|---|
| `QWEN_SERVER_URL` | `http://127.0.0.1:8080` | llama-server address (keep it on localhost) |
| `QWEN_MODEL_ID` | `qwen2.5-3b-instruct-q4_k_m` | label shown in the UI |
| `QWEN_CTX_SIZE` | 4096 | context length (`-c`); a planning prompt is ~800 tokens |
| `QWEN_THREADS` | 3 | CPU threads (`-t`); leaves one core for camera and sensors |
| `QWEN_MANAGE_SERVER` | false | let the backend start/stop llama-server itself |

| Model | Quant | RAM | Notes |
|---|---|---|---|
| Qwen2.5-3B-Instruct (`Qwen/Qwen2.5-3B-Instruct-GGUF`) | Q4_K_M ≈ 2 GB | ≈ 3 GB | default |
| Qwen2.5-1.5B-Instruct (`Qwen/Qwen2.5-1.5B-Instruct-GGUF`) | Q4_K_M ≈ 1 GB | ≈ 1.6 GB | about 2× faster — **recommended for live demos** |
| Qwen3-4B-Instruct-2507 (community GGUF) | Q4_K_M ≈ 2.5 GB | ≈ 3.5 GB | stronger, slower |

A task makes two Qwen calls: one plan and one report. On the Pi 5's CPU, expect a few
seconds to tens of seconds per call depending on the model. Skills like `track` run at the
detector rate without the LLM in the loop, so camera motion stays responsive. The chat shows the
measured time and tokens/s for every answer. The schema-constrained JSON planning depends on
llama-server's `response_format` support; use a current llama.cpp build.

```bash
scripts/build_llama_cpp.sh
scripts/download_qwen.sh                                      # 3B default
scripts/download_qwen.sh Qwen/Qwen2.5-1.5B-Instruct-GGUF qwen2.5-1.5b-instruct-q4_k_m.gguf   # faster
scripts/install_llama_service.sh ~/STRIX-1/models/qwen2.5-3b-instruct-q4_k_m.gguf 3 4096
```

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
