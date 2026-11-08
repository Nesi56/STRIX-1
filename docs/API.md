# API reference

Base URL: `http://<pi>:8000`. All `/api/*` endpoints except `health` and `auth/*` require
authentication: either the session cookie from `POST /api/auth/login`, or
`Authorization: Bearer <STRIX_AUTH_TOKEN>` for scripts. Interactive docs: `GET /api/docs`
(authenticated). Timestamps are UNIX seconds (server clock).

Errors use one shape: `{"error": {"code": "...", "message": "...", "details": [...]?}}`, with status
`401` unauthenticated, `403` forbidden/non-local network, `404`,
`409` state conflict (e.g. servo disabled, agent busy, already recording), `422` validation, `503` device unavailable.

## REST endpoints

| Method | Path | Body / query | Purpose |
|---|---|---|---|
| GET | `/api/health` | — | Liveness, version, demo-mode flag (public) |
| GET | `/api/auth/status` | — | `{auth_enabled, authenticated}` (public) |
| POST | `/api/auth/login` | `{token}` | Sets HttpOnly SameSite=Strict session cookie (rate limited) |
| POST | `/api/auth/logout` | — | Clears the cookie |
| GET | `/api/system` | — | CPU, RAM, temperature, throttling, PMIC, Wi-Fi, processes, warnings |
| GET | `/api/config` | — | Non-secret configuration summary |
| GET | `/api/events` | `limit` | Recent device/operator events |
| GET | `/api/logs` | `limit` | Recent backend log records |
| GET | `/api/sensors` | — | Latest IMU + DHT11 samples and all device statuses |
| GET | `/api/imu` | — | IMU summary: latest sample, calibration, health findings, limitations |
| GET | `/api/imu/history` | `seconds`, `max_points` | Decimated (25 Hz) IMU history |
| POST | `/api/imu/calibrate` | `{duration_s: 1–30, level: bool}` | Static calibration (airframe still); 409 if motion detected |
| GET / DELETE | `/api/imu/calibration` | — | Calibration status / clear |
| POST | `/api/imu/reset-yaw` | — | Zero the relative yaw |
| GET | `/api/environment` | — | DHT11 summary, session min/max, 5-min trend |
| GET | `/api/environment/history` | `seconds` | DHT11 history |
| POST | `/api/environment/reset-extremes` | — | Reset session min/max |
| GET | `/api/camera/status` | — | Camera state, measured FPS, sensor modes, recording |
| GET | `/api/camera/stream.mjpg` | `fps` (optional cap) | MJPEG multipart stream with `X-Frame-Seq`, `X-Capture-Time`, `X-Synthetic` |
| GET | `/api/camera/frame.jpg` | — | Latest single frame |
| POST | `/api/camera/snapshot` | — | Save the current frame → `{id, ts, width, height, synthetic}` |
| GET | `/api/camera/snapshots` · `/{name}` | — | List / download snapshots (DELETE `/{name}` removes) |
| POST | `/api/camera/recording/start` · `/stop` | — | H.264 MP4 recording |
| GET | `/api/camera/videos` · `/{name}` | — | List / download recordings (DELETE `/{name}` removes) |
| POST | `/api/camera/config` | `{width, height, fps}` | Change stream mode (must be supported by a sensor mode) |
| POST | `/api/camera/webrtc/whep` | SDP offer (`application/sdp`) | Optional WHEP proxy to MediaMTX |
| GET | `/api/servo` | — | Servo state, limits, armed flag |
| POST | `/api/servo/arm` · `/disarm` | — | Enable / disable camera servo control |
| POST | `/api/servo/angle` | `{angle: 0–180}` | Set target (clamped to safe limits, slew limited); 409 if disarmed |
| POST | `/api/servo/center` | — | Move to the center angle |
| POST | `/api/servo/limits` | `{min_angle, max_angle, max_speed_dps, center_angle?}` | Update and persist safe limits |
| POST | `/api/servo/nudge` | `{up_deg: −90…90}` | Tilt relative to the current target (+ = up) |
| POST | `/api/servo/speed` | `{max_speed_dps}` | Change the slew-rate limit |
| POST | `/api/estop` | — | **Emergency stop**: disable servo control and cancel the agent |
| POST | `/api/session/reset` | — | Cancel the agent, clear chat, zero yaw, reset environment min/max |
| GET | `/api/detector` | — | Detector engine, FPS, inference time, prompts, latest detections |
| GET | `/api/detector/classes` | — | Classes the active detector knows (COCO-80, or `person` for HOG) |
| POST | `/api/detector/prompts` | `{prompts: [..]}` | Classes drawn on the video (422 if unknown) |
| GET | `/api/memory` | — | Object memory: `objects[]` (SEEN / REMEMBERED / HIDDEN, `out_of_sight_s`, `contained_by`, direction `az`/`el`, `range_m`, `xyz`, `projected_bbox`), `events[]`, `counts`, camera pose, demo scene |
| POST | `/api/memory/clear` | — | Forget everything |
| POST | `/api/memory/aim` | `{target: "sports ball" \| "#4"}` | Aim the camera **tilt servo** at a remembered object; returns yaw/distance advice for the pilot. 404 if not in memory, 409 if servo control is disabled |
| GET / POST | `/api/demo/scene` | `{scene: "cups" \| "outdoor"}` | Synthetic mode only: shell-game or outdoor demo scene (409 on real hardware) |
| GET | `/api/ai/status` | — | Qwen / llama-server status |
| POST | `/api/agent/run` | `{task}` | Run a Qwen agent task — streaming NDJSON (below); 409 if busy |
| POST | `/api/agent/stop` | — | Cancel the running task |
| POST | `/api/agent/watch` | `{enabled}` | Qwen comments automatically on new YOLO detections (WATCH mode) |
| POST | `/api/record/start` | `{name?, notes?}` | **One-button recording**: telemetry session + camera video together |
| POST | `/api/record/stop` | — | Stop both; the video file name is stored in the session's metadata |
| GET | `/api/agent` | — | Agent state (status, active node, plan) |
| GET / DELETE | `/api/agent/history` | `limit` | Chat history / clear |
| POST | `/api/ai/qwen/{start\|stop\|restart}` | — | Control llama-server when `QWEN_MANAGE_SERVER=true` |
| GET | `/api/mapping` | — | Position status (unavailable), localization sources, VO state |
| POST | `/api/mapping/vo` · `/vo/reset` | `{enabled}` | Start/stop/reset experimental visual odometry |
| GET | `/api/recordings` | — | Sessions, videos, snapshots, recorder status |
| GET | `/api/recordings/status` | — | Active session info |
| POST | `/api/recordings/start` · `/stop` | `{name?, notes?}` | Telemetry recording session |
| POST | `/api/recordings/retention` | — | Apply retention now |
| GET / PATCH / DELETE | `/api/recordings/{id}` | `{name?, notes?}` | Session metadata |
| GET | `/api/recordings/{id}/telemetry` | `start`, `end`, `max_points` | Downsampled rows + events for playback |
| GET | `/api/recordings/{id}/export.csv` · `export.json` | — | Streaming download |

### `POST /api/agent/run`

```json
{ "task": "Find the person and keep them centered" }
```

The response is `application/x-ndjson`, one event per line:

| `type` | Fields |
|---|---|
| `run` | `run_id`, `task` |
| `node` | `node`: `generator` · `observer` · `pilot` · `until` · `report` (drives the agent graph) |
| `plan` | `planner` (`qwen` or `rules`), `thought`, `target`, `steps[]` (`skill`, args, `text`, `status`), `stats` |
| `step` | `index`, `status` (`running` · `done` · `failed` · `skipped`), `text` (result) |
| `progress` | `text` (e.g. scan progress per tilt angle) |
| `observation` | `text` (objects seen before a step) |
| `suggestions` | `items[]` — deterministic movement advice for the pilot |
| `vision` | `image_sent` (camera frame attached for the vision model), `vision_capable` |
| `delta` / `replace` | report text (sanitized) |
| `error` | `code`, `message` (e.g. Qwen report failed → rule-based report follows) |
| `done` | `planner`, `reporter`, `stats` (`latency_ms`, `tokens_per_s`, …), `total_ms` |
| `stopped` | `reason` (e.g. `EMERGENCY STOP`) |

## WebSocket `/ws/telemetry`

Authentication: session cookie (browsers) or `?token=<STRIX_AUTH_TOKEN>` (scripts). A cross-origin
`Origin` is rejected; an unauthenticated connection closes with code 4401.

Server → client (JSON text frames):

- `{"type":"hello","server_time","version","telemetry_hz","demo_mode","snapshot":{…full state…}}`
- `{"type":"telemetry","ts","seq","demo_mode","imu":{"status","latest"},"agent":{…}}` at `STRIX_TELEMETRY_HZ` (20 Hz),
  plus `"detections"` whenever the detector has a new result (`detections[]` with `label`, `confidence`,
  normalized `bbox`, `distance_m`, `distance_kind`, `bearing_deg`, `elevation_deg`).
  plus `"memory"` (same shape as `GET /api/memory`) whenever it changes and at least once per second.
  Once per second the message also has `"full": true` plus `imu_health`, `imu_calibration`,
  `environment`, `servo`, `camera`, `system`, `ai`, `detector`, `mapping`, `recording`.
- `{"type":"event","event":{"ts","level","source","message"}}`
- `{"type":"chat","message":{"id","ts","auto":true,"provider":"qwen|rules","vision","text"}}` (WATCH-mode comments on YOLO detections and memory events such as "hidden under cup #2")
- `{"type":"pong","client_ts","server_ts"}` (milliseconds) in reply to a ping.

Client → server: `{"type":"ping","client_ts": <ms>}`. The dashboard pings every 2 s for RTT and
clock-offset estimation.

Each client has a bounded queue (10 messages). A slow client drops its oldest messages rather
than delaying others. The browser reconnects automatically with backoff (0.5 → 10 s) and
immediately when the network comes back (`online` event) or the tab becomes visible.

### Example (Python)

```python
import asyncio, json, websockets
async def main():
    async with websockets.connect("ws://strix1.local:8000/ws/telemetry?token=YOUR_TOKEN") as ws:
        async for raw in ws:
            m = json.loads(raw)
            if m["type"] == "telemetry" and m["imu"]["latest"]:
                o = m["imu"]["latest"]["orientation"]
                print(f"roll {o['roll']:+.1f}  pitch {o['pitch']:+.1f}")
asyncio.run(main())
```
