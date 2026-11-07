# API reference

Base URL: `http://<pi>:8000`. All `/api/*` endpoints except `health` and `auth/*` require
authentication: either the session cookie from `POST /api/auth/login`, or
`Authorization: Bearer <STRIX_AUTH_TOKEN>` for scripts. Interactive docs: `GET /api/docs`
(authenticated). Timestamps are UNIX seconds (server clock).

Errors use one shape: `{"error": {"code": "...", "message": "...", "details": [...]?}}`, with status
`401` unauthenticated, `403` forbidden/consent required/non-local network, `404`,
`409` state conflict (e.g. servo disabled, already recording), `422` validation, `503` device unavailable.

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
| GET | `/api/ai/models` | — | Modes and provider status (Qwen/Grok) |
| POST | `/api/ai/chat` | `ChatRequest` (below) | Streaming NDJSON answer |
| GET / DELETE | `/api/ai/history` | `limit` | Chat history / clear |
| GET | `/api/ai/usage` | — | Token/cost/latency totals per provider |
| POST | `/api/ai/qwen/{start\|stop\|restart}` | — | Control llama-server when `QWEN_MANAGE_SERVER=true` |
| GET | `/api/mapping` | — | Position status, localization sources, VO state, vision status |
| POST | `/api/mapping/analyze` | `{snapshot_id?}` | Run OpenCV scene analysis now |
| GET | `/api/mapping/observations` | `limit` | Observation log |
| POST | `/api/mapping/auto` | `{interval_s}` | Automatic analysis interval (0 = off) |
| POST | `/api/mapping/vo` · `/vo/reset` | `{enabled}` | Start/stop/reset experimental visual odometry |
| GET | `/api/recordings` | — | Sessions, videos, snapshots, recorder status |
| GET | `/api/recordings/status` | — | Active session info |
| POST | `/api/recordings/start` · `/stop` | `{name?, notes?}` | Telemetry recording session |
| POST | `/api/recordings/retention` | — | Apply retention now |
| GET / PATCH / DELETE | `/api/recordings/{id}` | `{name?, notes?}` | Session metadata |
| GET | `/api/recordings/{id}/telemetry` | `start`, `end`, `max_points` | Downsampled rows + events for playback |
| GET | `/api/recordings/{id}/export.csv` · `export.json` | — | Streaming download |

### `POST /api/ai/chat`

```json
{
  "message": "Is the IMU behaving abnormally?",
  "mode": "local | grok | hybrid",
  "deep_analysis": false,
  "external_consent": false,
  "include_telemetry": true,
  "snapshot_id": null,
  "history_turns": 4
}
```

A Grok route (`mode=grok`, or `hybrid` + `deep_analysis`) without `external_consent=true` → `403 consent_required`.
The response is `application/x-ndjson`, one event per line:

| `type` | Fields |
|---|---|
| `meta` | `provider`, `model`, `label`, `local`, `external`, `telemetry_shared`, `image_sent`, `notes`, `synthetic_sources` |
| `delta` | `text` (sanitized incremental text) |
| `replace` | `text` (full text, when sanitization changed already-streamed content) |
| `error` | `code` (`not_ready`, `timeout`, `auth`, `rate_limited`, …), `message`, `provider` |
| `fallback` | `provider: "strix-digest"`, `label`, `text` (deterministic digest when the local model is unavailable) |
| `done` | `stats` (`latency_ms`, `ttft_ms`, `tokens_per_s`, `prompt_tokens`, `completion_tokens`, `cost_usd`), `sanitized` |

## WebSocket `/ws/telemetry`

Authentication: session cookie (browsers) or `?token=<STRIX_AUTH_TOKEN>` (scripts). A cross-origin
`Origin` is rejected; an unauthenticated connection closes with code 4401.

Server → client (JSON text frames):

- `{"type":"hello","server_time","version","telemetry_hz","demo_mode","snapshot":{…full state…}}`
- `{"type":"telemetry","ts","seq","demo_mode","imu":{"status","latest"}}` at `STRIX_TELEMETRY_HZ` (20 Hz).
  Once per second the message also has `"full": true` plus `imu_health`, `imu_calibration`,
  `environment`, `servo`, `camera`, `system`, `ai`, `mapping`, `recording`.
- `{"type":"event","event":{"ts","level","source","message"}}`
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
