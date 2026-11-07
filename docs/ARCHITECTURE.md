# Architecture

## Process and threading model

One Python process (FastAPI on uvicorn), with an optional separate `llama-server` process and
an optional MediaMTX process.

| Component | Runs in | Rate | Talks to |
|---|---|---|---|
| `imu_service` | own thread | 100 Hz | MPU6050 (I2C); latest sample + 25 Hz ring buffer |
| `dht_service` | own thread | every 3 s | kernel IIO sysfs or Adafruit driver |
| `servo_service` | own thread | 50 Hz trajectory loop | gpiozero / sysfs PWM |
| `camera_service` | Picamera2 encoder threads | camera FPS | JPEG → `LatestValueHub`; H.264 → MP4 |
| `system_monitor` | own thread | 0.5 Hz (vcgencmd 0.2 Hz) | psutil, sysfs, vcgencmd |
| `telemetry_service` | asyncio task | 20 Hz (+1 Hz full) | WebSocket clients via bounded queues |
| `recording_service` | asyncio task + SQLite writer thread | 10 Hz | batched inserts (WAL) |
| `mapping_service` | asyncio + worker threads | on demand / auto / VO 5 Hz | OpenCV |
| `ai_qwen_service` | asyncio (httpx) | on demand, serialized | llama-server on localhost |
| `ai_grok_service` | asyncio (httpx) | on demand, rate limited | api.x.ai |

Blocking hardware I/O never runs on the event loop. Threads publish to lock-protected
"latest value" slots. The loop reads them, and events cross into the loop with
`call_soon_threadsafe`. Device failures are contained: each backend reports a `DeviceStatus`
(`ok`, `synthetic`, `degraded`, `disconnected`, `disabled`, `error`). Acquisition threads retry
initialisation periodically, so re-plugging a sensor recovers without a restart.

## Data flow

```
MPU6050 ──burst read──▶ axis map ──▶ calibration ──▶ complementary filter ──▶ latest/ring ──▶ WS 20 Hz
DHT11  ──IIO sysfs──▶ plausibility check ──▶ latest/history/session extremes ──────────────▶ WS 1 Hz
Camera ──JpegEncoder──▶ LatestValueHub ──▶ /api/camera/stream.mjpg (each viewer: newest frame only)
       ──H264Encoder──▶ PyavOutput (MP4)       ──lores──▶ OpenCV analysis / VO
All    ──recorder 10 Hz──▶ SQLite (sessions, telemetry, events, observations, chat, ai_usage)
AI     ◀── ai_context (timestamped digest) ◀── all services
```

## Orientation conventions

Sensor/body frame after axis mapping: X forward, Y left, Z up. The filter converts to the
aerospace FRD frame internally, so the outputs follow aviation conventions: roll + right side
down, pitch + nose up, relative yaw + nose right. The accelerometer correction is attenuated
when |a| departs from 1 g (dynamic acceleration). The 3D view applies Euler order YXZ with a 40 ms
visual smoothing constant, which can be turned off. Numeric readouts are never smoothed.

## Safety design

- **No flight control.** No module has an interface to motors, ESCs, flight controllers or
  navigation. The servo module drives one PWM pin for the camera tilt only.
- **Servo.** Disarmed at boot; safe limits clamp every command; 50 Hz slew limiter; disarm
  stops motion immediately; pulses are released when idle. The SG90 has no position feedback,
  so angles are labeled *commanded*.
- **Honest data.** Mock backends are opt-in (`STRIX_MOCK` or `*_BACKEND=mock`) and every
  synthetic sample, frame, file and session is labeled. A missing device reports
  *disconnected*, never fake data. Position is *unavailable* without a validated source. IMU
  acceleration is never integrated into position. Battery % is not estimated.
- **AI.** Grounded context, claim tags, no tools/actions, shell-command filtering, no image
  sent to text-only models, explicit consent for any cloud request, no automatic uploads.
- **Network.** Private-network allow-list, token auth with HMAC-signed HttpOnly SameSite=Strict
  cookies, WebSocket origin check, login rate limiting, secrets only in `.env` (never returned
  by the API, never logged), media path-traversal protection.
- **Resources.** llama-server is serialized, runs at lower priority, and can be capped by
  `MemoryMax`. Low-memory, thermal and under-voltage warnings are shown in the dashboard and
  available to the AI.

## Extending localization (Mode C)

`mapping_service.LocalizationSource` describes each source: `installed`, `running`,
`validated`, `experimental`, `frame`. Only a **validated** running source may provide a position
(e.g. a GNSS fix with quality checks, or a VIO system validated against ground truth). Visual
odometry is registered as experimental and is never validated by default. To add GPS, implement
a reader (gpsd/NMEA), register a source, set `validated` only after quality gating, and extend
`MappingService.position()`.
