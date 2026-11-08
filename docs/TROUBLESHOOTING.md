# Troubleshooting

Logs first: `journalctl -u strix1 -f` (or the terminal running `scripts/run_backend.sh`),
`~/STRIX-1/data/logs/strix1.log`, and the console's *Recordings* view → *Logs & Events → Backend log*.

## Access / network

| Symptom | Check / fix |
|---|---|
| `strix1.local` doesn't resolve | Use the IP (`hostname -I` on the Pi). On Windows, install Bonjour or use the IP. Some guest Wi-Fi networks block mDNS and client-to-client traffic. |
| Page loads but "Connection to STRIX-1 lost" | The WebSocket is blocked or flapping. Check `curl http://<pi>:8000/api/health`. A reverse proxy must forward WebSocket upgrades on `/ws/`. The banner clears automatically when the link returns. |
| `403 client network not allowed` | Your client IP is outside `STRIX_ALLOWED_NETWORKS` (e.g. a public IPv6 address). Add your subnet, or use the Pi's IPv4 address. |
| Login says invalid token | Use `STRIX_AUTH_TOKEN` from `~/STRIX-1/.env` (or `data/auth_token` if it was auto-generated). Rotating the token invalidates all sessions. |
| "429 too many login attempts" | Wait 60 s (10 attempts/min per client). |
| Dashboard shows "dashboard has not been built" | Build `frontend/dist` (see SETUP_PI.md §2) and restart. |

## Camera

| Symptom | Check / fix |
|---|---|
| *DISCONNECTED: no camera detected* | `rpicam-hello --list-cameras`. Reseat the ribbon (contacts face the right way, latch closed), use the Pi 5 22-pin cable, and make sure `camera_auto_detect=1` is set. |
| `Device or resource busy` | Another process holds the camera (`rpicam-*`, another STRIX instance, MediaMTX `rpiCamera` source). Stop it; only one process can open the camera. |
| `picamera2` import fails in the venv | The venv must be created with `--system-site-packages`, and `python3-picamera2` installed with apt. Don't `pip install picamera2` or numpy into the venv. |
| `numpy.dtype size changed` / binary incompatibility | A pip-installed numpy shadows the system one: `backend/.venv/bin/pip uninstall numpy opencv-python opencv-python-headless`. |
| Low FPS / high latency | Check CPU on the System page. Lower the resolution (1280×720), lower JPEG quality, cap per-viewer FPS, stop the recording, or move to 5 GHz Wi-Fi. |
| 60 fps not offered | No sensor mode supports it at that resolution (IMX708: 720p60 yes, 1080p60 no). Run `scripts/verify_camera.sh`. |
| Recording is a `.h264` file | PyAV is missing (`sudo apt install python3-av`). Raw H.264 plays in VLC or converts with `ffmpeg -i x.h264 -c copy x.mp4`. |

## A row in SYSTEM STATUS is red

The line under each red row shows the backend's exact reason (hover for the full text). The usual ones:

| Row / message | Fix |
|---|---|
| MPU6050 · `/dev/i2c-1 missing` or `No such file` | I2C is off: `sudo raspi-config nonint do_i2c 0`, then reboot. |
| MPU6050 · `No module named 'board'` | Hardware libraries are missing: `backend/.venv/bin/pip install -r backend/requirements-pi.txt` |
| MPU6050 · `Remote I/O error` / `Failed to find MPU6050` | Wiring: VIN→pin 1, GND→pin 6, SDA→pin 3, SCL→pin 5; check `i2cdetect -y 1` shows `68`. |
| DHT11 · `no dht11 IIO device found` | Add `dtoverlay=dht11,gpiopin=4` to `/boot/firmware/config.txt` and reboot (or run `scripts/install_pi.sh --skip-apt --skip-frontend --configure-boot`). |
| QWEN · `not running` | `scripts/setup_ai.sh` (installs and starts it). Logs: `journalctl -u strix1-llama -f`. |
| YOLO · `HOG fallback` | `scripts/get_detector_model.sh`, then restart. |
| BATTERY · `no fuel gauge` | Expected: a USB power bank does not report its charge to the Pi. The row still warns about under-voltage. |

Then run `scripts/verify_all.sh` (stop STRIX-1 first so the camera is free) and compare.

## IMU (MPU6050)

| Symptom | Check / fix |
|---|---|
| DISCONNECTED, `/dev/i2c-1` missing | Enable I2C (`sudo raspi-config nonint do_i2c 0`) and reboot. |
| `i2cdetect -y 1` shows nothing | Wiring: VIN→pin 1 (3.3 V), GND→6, SDA→3, SCL→5. Check for a loose jumper. |
| Shows `69` instead of `68` | AD0 is high → `STRIX_IMU_ADDRESS=0x69`. |
| "Failed to find MPU6050" | WHO_AM_I mismatch (clone chip). `scripts/verify_imu.py` prints the value. |
| Permission denied | Add the user to the `i2c` group and log in again. |
| Roll/pitch signs wrong | Fix `STRIX_IMU_AXIS_MAP` (see HARDWARE.md); the nose-up check must give positive pitch. |
| Yaw keeps drifting | Expected: there's no magnetometer. Recalibrate on a still surface and zero yaw. |
| `accel_magnitude` / `gyro_bias` warnings | Recalibrate with the airframe level and still; check mounting and vibration isolation. |
| Low sample rate | I2C bus contention or CPU saturation. The default bus speed (100 kHz) supports ~100 Hz. |

## DHT11

| Symptom | Check / fix |
|---|---|
| No IIO device | `dtoverlay=dht11,gpiopin=4` must be in `/boot/firmware/config.txt`; reboot; `ls /sys/bus/iio/devices/`. |
| Frequent read failures | Normal occasionally (EIO/ETIMEDOUT). Check the pull-up, keep the leads short, keep `STRIX_DHT_INTERVAL_S` ≥ 2. |
| Values stale | The sensor stopped answering; the panel shows *stale* and the status degrades to *disconnected* after 5 failures. |

## Servo

| Symptom | Check / fix |
|---|---|
| SERVO row red: "cannot open the GPIO chip" | Usually the systemd service: lgpio needs a writable working directory. Re-run `scripts/install_pi.sh --skip-apt --skip-frontend --systemd` (adds `LG_WD`), or check that your user is in the `gpio` group (`--groups`, then reboot). |
| SERVO row red: "GPIO18 is already in use" | Remove any `dtoverlay=pwm…` line for GPIO18 from `/boot/firmware/config.txt` and reboot, or stop the other program using it. |
| SERVO row red: "GPIO library missing" / "no GPIO driver" | `sudo apt install python3-gpiozero python3-lgpio`, then `scripts/install_pi.sh --skip-frontend` (the venv must use the system packages). |
| SERVO row green but the servo doesn't move | The Pi can't sense an SG90 (no feedback wire), so "on" only means the signal is being sent. Check the wiring: orange → **pin 12 (GPIO18)**, red → 52Pi **+5V OUTPUT**, brown → 52Pi **GND**, and the 52Pi GND must be common with the Pi. Test with `backend/.venv/bin/python scripts/verify_servo.py`. |
| "EMERGENCY STOP is active" | Press **Servo on** under the video (or **E**) to clear it. |
| Servo jitters / hums | Software PWM jitter (gpiozero/lgpio): keep `STRIX_SERVO_DETACH_WHEN_IDLE=true` or switch to hardware PWM. A weak supply or missing common ground also causes jitter. |
| Pi reboots / under-voltage when the servo moves | The servo is drawing from the Pi's supply. Use a separate regulated 5 V supply with common ground. |
| Hits an end-stop | Narrow `STRIX_SERVO_MIN/MAX_ANGLE` (Calibration view) or adjust the pulse range. |

## AI

| Symptom | Check / fix |
|---|---|
| Qwen *UNREACHABLE* | `systemctl status strix1-llama`; `curl http://127.0.0.1:8080/health`; `QWEN_SERVER_URL` must match. |
| Qwen *LOADING* for a long time | Large model on SD card: first load reads ~2 GB. Use an NVMe/USB SSD for models. |
| Very slow answers | Lower `QWEN_CTX_SIZE` and `QWEN_MAX_TOKENS`, use a 3B Q4 model, reduce camera load, and keep `QWEN_THREADS=3`. |
| Answer contains "command block removed" | The model produced a shell/command block. It was removed on purpose. |
| Chat says "RULE PLAN" | Qwen was not ready (loading/unreachable) or returned unusable JSON, so the keyword planner was used. Check llama-server. |
| Qwen says "EMERGENCY STOP is active" | An emergency stop latches the servo off. Press **Servo on** (under the video) or **E**. |
| Camera tilts the wrong way | Set `STRIX_SERVO_UP_IS_POSITIVE=false` and restart. |
| "agent is already running" (409) | Wait for the task or press **Esc** / *Stop*. |

## Object detection

| Symptom | Check / fix |
|---|---|
| Prompts panel says HOG fallback | No ONNX model in `models/`. Run `scripts/get_detector_model.sh`. |
| YOLO FPS very low | YOLOX-S is heavy for the Pi 5 CPU. Use `--yolo11n` (lighter), lower `STRIX_DETECTOR_HZ`, or reduce Qwen threads while detecting. |
| Prompt rejected | Only COCO-80 class names work (e.g. `person, car, dog, chair, bottle, cell phone`). |
| Distances look wrong | Estimates assume a standing, fully visible object (person 1.7 m). Sitting people or cut-off boxes are farther/closer than shown. Check `STRIX_CAMERA_HFOV_DEG` (102 for Camera Module 3 *Wide*, ~66 for the standard module). |

## Object memory

| Symptom | Check / fix |
|---|---|
| Nothing appears in Memory | Only the YOLO targets (the row above the Qwen chat) plus containers (cup, bowl, vase, wine glass) are remembered, after 2 detections. Add the object there. |
| Remembered objects slowly rotate around the drone | Relative yaw drifts until the IMU is calibrated: press **Calibrate** with the drone still and level. |
| The ball isn't marked HIDDEN when covered | The cup must be detected as `cup` (or bowl/vase/wine glass) while the ball disappears behind it, so keep both in view. Real YOLO needs ≥ 2 detections/s; check the YOLO FPS. |
| After shuffling, memory points at the wrong cup | Identical cups keep identity only through continuous tracking: shuffle slower (≈ 3 s per swap) or raise the detector rate. Lifting the right cup corrects the memory ("memory corrected" event). |
| AIM / "where is the ball?" doesn't move the camera | Check the SERVO row in Status. The servo only tilts: turn the drone as Qwen says. |

## Power / thermals

| Symptom | Check / fix |
|---|---|
| UNDER-VOLTAGE banner | The PD source isn't delivering 5 V/5 A, or the servo/USB load is too high. `scripts/verify_power.sh`. |
| CPU ≥ 80 °C, throttled | Fit the Pi 5 active cooler; reduce stream resolution or LLM threads. |
| Battery shows "Unavailable" | By design: no battery telemetry source exists (see HARDWARE.md → Power). |

## Resetting state

All runtime state lives in `~/STRIX-1/data/`: the SQLite DB, media, logs, IMU calibration and
servo limit overrides. Stop the service and move that folder aside to start fresh. Nothing
outside `data/` and `.env` is modified at runtime.
