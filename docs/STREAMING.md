# Video streaming

## Default: MJPEG over HTTP (`GET /api/camera/stream.mjpg`)

- Picamera2 runs a `JpegEncoder` (simplejpeg, 2 threads) on the `main` stream (YUV420), so
  frames are encoded once and shared by all viewers.
- The backend keeps only the **newest** frame in a `LatestValueHub`. A slow viewer skips frames
  instead of building a backlog, so latency stays bounded on weak Wi-Fi.
- Each multipart part carries `X-Frame-Seq`, `X-Capture-Time` (sensor capture time on the
  server clock, via libcamera `SensorTimestamp` / `CLOCK_BOOTTIME`) and `X-Synthetic`.
- The dashboard reads the stream with `fetch()` (not `<img>`), parses parts
  (`frontend/src/services/mjpeg.ts`), decodes only the newest frame with `createImageBitmap`
  and draws it on a canvas.
- `?fps=N` caps the per-viewer rate to save bandwidth (selector: Full / 15 / 10 / 5 fps).

### Latency measurement

`latency = (browser_now + clock_offset) − capture_time`, measured after the frame is decoded.
The clock offset comes from NTP-style pings over the telemetry WebSocket (minimum-RTT sample,
`frontend/src/services/clock.ts`). The result is a *glass-to-browser* estimate covering
exposure readout, encoding, network and decoding. Display scan-out is not included. In mock
mode the capture time is the render time.

### Bandwidth

At JPEG quality 80, a 1080p frame is roughly 150–300 kB and 720p roughly 60–120 kB, depending
on the scene. At 30 fps that is about 15–60 Mbit/s for 1080p. That is fine on a close 5 GHz
link but heavy on 2.4 GHz. Lower the resolution, `STRIX_CAMERA_JPEG_QUALITY` or the per-viewer
FPS cap if the stream stutters.

## CPU budget

The Pi 5 **has no hardware video encoder**. Picamera2 maps `H264Encoder`/`MJPEGEncoder` to
libav software encoders on this platform (verified in Picamera2 0.3.39 source). Rough guide:

| Load | CPU |
|---|---|
| MJPEG 1080p30 stream | ~1 core |
| H.264 1080p30 recording (libx264) | ~1–1.5 cores |
| Qwen 3B generation (`QWEN_THREADS=3`) | 3 cores while generating |
| OpenCV analysis / VO | short bursts / ~0.3 core at 5 Hz |

Running everything at 1080p at once will saturate the CPU and slow LLM output. Options:
stream at 1280×720, record only when needed, or pause the stream during long AI analyses.
**These numbers are estimates to validate on the hardware**; the System page shows per-core load.

## Recording

`POST /api/camera/recording/start` attaches an H.264 encoder with a `PyavOutput` MP4 (fallback:
raw `.h264`) to the running camera. Streaming continues uninterrupted. Files go to
`data/media/videos/`. You can't change resolution while recording.

## Optional: WebRTC via MediaMTX (experimental)

For lower latency and bandwidth, the backend can push H.264 to a local
[MediaMTX](https://github.com/bluenviron/mediamtx) instance. The browser then receives WebRTC
(WHEP), with signalling proxied through the authenticated backend.

1. Install MediaMTX (arm64 release binary) and run it with [`config/mediamtx.yml`](../config/mediamtx.yml).
2. In `.env` set `STRIX_WEBRTC_ENABLED=true` (RTSP/WHEP URLs default to localhost).
3. Restart STRIX-1. The camera panel then offers *Transport: WebRTC (exp.)*. MJPEG stays available.

The browser must reach the Pi on UDP 8189. Latency is shown as a receiver-side estimate (jitter
buffer + RTT/2). This path costs an extra continuous H.264 encode and **has not been validated
on hardware**. MJPEG is the supported default.
