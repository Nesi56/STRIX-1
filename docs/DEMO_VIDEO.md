# STRIX-1 — 90-second demo video

`STRIX-1_demo_90s.mp4`: 1920×1080, 25 fps, 1:30, H.264 with a silent audio track (ready for YouTube/Vimeo,
which Devpost links to). It was recorded from the real console in **demo mode**: the camera and sensors
are simulated and labelled so on screen. In the recording Qwen isn't installed, so STRIX-1's built-in
fallback writes the chat replies; the captions say so.

The video has burned-in captions. Record the voiceover below over it (YouTube Studio, CapCut, Canva or
iMovie), at a relaxed pace of about 2.3 words per second.

## Timeline and voiceover

| Time | On screen | Voiceover |
|---|---|---|
| 0:00–0:05 | Title card: STRIX-1, "AI co-pilot for a Raspberry Pi 5 drone" | "This is STRIX-1: an AI co-pilot that runs entirely on the drone." |
| 0:05–0:15 | **01 · One screen** — the whole console | "Everything here runs on the drone's Raspberry Pi 5: the live camera, the sensors, the memory, a 3D view and a local Qwen model. No cloud." |
| 0:15–0:27 | **02 · Sees** — zoom into the camera, YOLO boxes with distances | "YOLO spots objects in the camera feed, and STRIX-1 estimates how far away each one is from the lens geometry. These cups are about seventy centimetres away." |
| 0:27–0:39 | **03 · Feels** — the 3D drone with roll, pitch, yaw, temperature and humidity | "The MPU6050 IMU is read a hundred times a second, so the 3D drone tilts as the real one does. The DHT11 adds temperature and humidity." |
| 0:39–0:52 | **04 · Aims** — Camera Servo panel, then the chat "look up 20 degrees" | "The camera rides on an SG90 servo. Tilt it in five-degree steps, or just ask Qwen to look up twenty degrees: it plans the move and the servo follows, within safe limits." |
| 0:52–1:14 | **05 · Remembers** — the shell game at 1.5× speed | "Now the shell game. A cup covers the ball; the memory marks it hidden. The cups are shuffled and the ball moves with its cup. When the camera looks away, everything stays remembered, with timers. Ask where the ball is: the camera aims back at the right cup. Lift it: memory was right." |
| 1:14–1:26 | **Why it matters** — four points | "Why it matters: it keeps track of people and objects, which could help search and rescue. It's private and offline, built from parts anyone can get, and honest about what it doesn't know." |
| 1:26–1:30 | End card: STRIX-1, github.com/Nesi56/STRIX-1 | "STRIX-1. Thanks for watching." |

## Make it again (after changes to the console)

Needs Node with Playwright, Python 3 with Pillow, and ffmpeg.

```bash
STRIX_MOCK_SCENE=cups STRIX_AUTH_TOKEN=demo123 scripts/run_mock.sh
```

```bash
STRIX_URL=http://127.0.0.1:8000 STRIX_TOKEN=demo123 node tools/demo_video/record.cjs /tmp/strix-video/take
```

```bash
python3 tools/demo_video/render.py /tmp/strix-video
```

```bash
cd /tmp/strix-video && ffmpeg -framerate 25 -i out/%05d.jpg -f lavfi -i anullsrc=channel_layout=stereo:sample_rate=48000 -shortest -c:v libx264 -preset slow -crf 18 -pix_fmt yuv420p -c:a aac -b:a 128k -movflags +faststart STRIX-1_demo_90s.mp4
```

`record.cjs` drives the console through each feature and saves timestamped frames; `render.py` does the
edit (zooms, captions, title and impact cards, crossfades). Captions and timing live at the top of
`render.py`.

## Showing the real drone instead

For the strongest submission, swap segments 03 and 04 for phone footage of the real drone: tilt it by
hand while the 3D model follows, and show the servo moving the camera. Keep the console clips for the
memory demo. The shot list in [DEVPOST.md](DEVPOST.md) covers this.
