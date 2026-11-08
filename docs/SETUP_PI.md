# Raspberry Pi 5 setup guide

Target: Raspberry Pi 5 (16 GB), **Raspberry Pi OS 64-bit** (Bookworm or newer), headless over Wi-Fi.
Every command runs on the Pi via SSH unless marked *(laptop)*.

## 1. Flash and first boot

1. *(laptop)* In **Raspberry Pi Imager** choose *Raspberry Pi 5* → *Raspberry Pi OS (64-bit)*
   (Lite is fine; the dashboard is served to your laptop's browser).
2. Open *OS customisation* and set:
   - a **hostname** (e.g. `strix1`), username and password
   - **Wi-Fi** SSID/password and country
   - **Enable SSH**
3. Boot the Pi, then from the laptop run `ssh <user>@strix1.local`.
4. Update:
   ```bash
   sudo apt update && sudo apt full-upgrade -y && sudo reboot
   ```

> Power: the Pi 5 wants a 5 V / 5 A USB-PD source. With a weaker source it limits USB
> peripheral current and may under-volt under load. Check with `scripts/verify_power.sh`
> (see [HARDWARE.md](HARDWARE.md#power)).

## 2. Get the code and install

```bash
sudo apt install -y git
git clone https://github.com/Nesi56/STRIX-1.git ~/STRIX-1
cd ~/STRIX-1
scripts/install_pi.sh
```

What `install_pi.sh` does by default (it asks before using apt):

| Step | Change |
|---|---|
| apt packages | `python3-venv python3-dev python3-pip python3-picamera2 python3-av python3-opencv python3-numpy python3-lgpio python3-gpiozero python3-libgpiod i2c-tools rpicam-apps git curl build-essential` |
| Python venv | `backend/.venv`, created **with `--system-site-packages`** so the apt-provided Picamera2/libcamera, NumPy, OpenCV, gpiozero and lgpio are used (pip-installing these over the system copies breaks the libcamera ABI). It then installs `backend/requirements-pi.txt` (FastAPI, uvicorn, pydantic, httpx, psutil, Adafruit Blinka/MPU6050/DHT, smbus2). |
| `.env` | Created from `.env.example` if missing, with a random `STRIX_AUTH_TOKEN` (printed once). Mode 600. |
| Dashboard | Built with `npm ci && npm run build` **if Node ≥ 20.19 is installed**; otherwise it prints instructions. |

Optional flags make **system changes**, and each one asks first:

- `--configure-boot`: enables I2C (`raspi-config nonint do_i2c 0`) and appends the block in
  [`config/boot-config.txt`](../config/boot-config.txt) to `/boot/firmware/config.txt`. A timestamped backup is
  made first. The block contains `dtparam=i2c_arm=on` and `dtoverlay=dht11,gpiopin=4`.
- `--groups`: adds your user to `i2c`, `gpio`, `video`.
- `--systemd`: installs and enables `strix1.service`.

Reboot after `--configure-boot` / `--groups`.

### Node.js for the dashboard build

Vite 8 needs Node ≥ 20.19; Raspberry Pi OS ships an older Node. Choose one option:

- **Build on the laptop** *(laptop)*:
  ```bash
  cd frontend && npm ci && npm run build
  rsync -a dist/ <user>@strix1.local:~/STRIX-1/frontend/dist/
  ```
- **Install Node 22 on the Pi** (adds the NodeSource apt repository, which is a system change):
  ```bash
  curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
  sudo apt install -y nodejs
  cd ~/STRIX-1/frontend && npm ci && npm run build
  ```

## 3. Enable interfaces manually (if you skipped `--configure-boot`)

```bash
sudo raspi-config nonint do_i2c 0                       # I2C on GPIO2/3
echo 'dtoverlay=dht11,gpiopin=4' | sudo tee -a /boot/firmware/config.txt
sudo usermod -aG i2c,gpio,video $USER
sudo reboot
```

The camera is auto-detected (`camera_auto_detect=1` is the default).

## 4. Verify the hardware

```bash
i2cdetect -y 1                                          # expect "68" (MPU6050)
ls /sys/bus/iio/devices/                                # expect iio:deviceN for the DHT11
rpicam-hello --list-cameras                             # expect imx708_wide
backend/.venv/bin/python scripts/verify_imu.py          # WHO_AM_I, |a| ≈ 1 g, rate, bias
backend/.venv/bin/python scripts/verify_dht.py          # ≥ 50 % successful reads
scripts/verify_camera.sh                                # sensor modes + measured FPS at 1080p30/720p60/1080p60
scripts/verify_power.sh                                 # throttling, 5 V input
backend/.venv/bin/python scripts/verify_servo.py        # interactive; external 5 V servo power!
```

Or run them together (servo excluded): `scripts/verify_all.sh`.
Record your results in [HARDWARE_VERIFICATION.md](HARDWARE_VERIFICATION.md).

## 5. Configure `.env`

`~/STRIX-1/.env` is the single configuration file. It is git-ignored; never commit it.
Key settings:

```ini
STRIX_HOST=0.0.0.0          # LAN access. Use 127.0.0.1 to restrict to the Pi itself.
STRIX_PORT=8000
STRIX_AUTH_TOKEN=…          # dashboard login token
STRIX_MOCK=false            # true = synthetic demo data for every device
STRIX_CAMERA_WIDTH=1920     # 1920x1080@30 initially; drop to 1280x720 if the CPU is busy
STRIX_SERVO_MIN_ANGLE=30    # safe travel limits — verify on your mount first
STRIX_SERVO_MAX_ANGLE=150
```

Restart after changes: `sudo systemctl restart strix1` (or re-run `scripts/run_backend.sh`).

## 6. AI: YOLO + local Qwen vision model (one command)

```bash
cd ~/STRIX-1 && scripts/setup_ai.sh
```

This installs the YOLO detector, builds llama.cpp, downloads Qwen2.5-VL-3B (vision), installs the
`strix1-llama` service, updates `.env` and restarts STRIX-1. You only do this once: after that Qwen starts by
itself on every boot (`install_pi.sh` offers to run it for you at the end). Run it inside `tmux`, since it takes 20–30 minutes.
Use `--fast` for the smaller Qwen2-VL-2B. When it finishes, the **QWEN** row in the dashboard's
SYSTEM STATUS corner turns green and reads `ready · vision`. Details: [AI.md](AI.md).

## 7. Object detection model (YOLO stream)

```bash
scripts/get_detector_model.sh             # YOLOX-S, Apache-2.0, checksum-verified → models/yolox_s.onnx
# optional, ~4x faster on the Pi (Ultralytics, AGPL-3.0; needs a temporary torch install):
scripts/get_detector_model.sh --yolo11n
```

The detector picks up `models/*.onnx` automatically (YOLO11/YOLOv8 preferred over YOLOX). Without
a model it falls back to OpenCV's person-only HOG detector. Check the YOLO FPS in the video's
top-right corner. Then test the servo direction with the ▲ button under the video (or **W**). The camera
must tilt **up**; if it tilts down, set `STRIX_SERVO_UP_IS_POSITIVE=false`.

## 8. Autostart with systemd

```bash
scripts/install_pi.sh --skip-apt --skip-frontend --systemd
systemctl status strix1
journalctl -u strix1 -f
```

The unit (`config/systemd/strix1.service`) runs as your user with the `video`, `i2c` and `gpio`
groups. It restarts on failure and uses filesystem hardening; only `~/STRIX-1/data` is writable.

## 9. Access from the laptop

Both devices must be on the same Wi-Fi network:

- `http://strix1.local:8000` (mDNS; works on macOS, Linux and Windows 10+), or
- `http://<pi-ip>:8000` — find the IP with `hostname -I` on the Pi or in your router.

Sign in with `STRIX_AUTH_TOKEN`. The session cookie lasts `STRIX_SESSION_TTL_HOURS` (72 h).

**No public exposure by default.** The backend rejects clients outside private/link-local
ranges (`STRIX_ALLOWED_NETWORKS`). Don't port-forward it. For remote access use a VPN such
as WireGuard or Tailscale (100.64.0.0/10 is allowed by default). Optional host firewall:

```bash
sudo apt install -y ufw
sudo ufw allow ssh
sudo ufw allow from 192.168.0.0/16 to any port 8000 proto tcp
sudo ufw enable
```

## 10. Development commands

| Command | Purpose |
|---|---|
| `scripts/run_mock.sh` | Demo with synthetic data (no hardware) |
| `scripts/run_dev.sh [--hardware]` | Backend + Vite dev server with hot reload (:5173) |
| `scripts/run_backend.sh` | Production backend in the foreground |
| `scripts/run_tests.sh` | All automated tests and the production build |
| `cd backend && .venv/bin/python -m pytest -q` | Backend tests only |
| `cd frontend && npm test` | Frontend unit tests |
| `http://<pi>:8000/api/docs` | Interactive OpenAPI docs (after login) |

## 11. Updating

```bash
cd ~/STRIX-1 && git pull
backend/.venv/bin/pip install -r backend/requirements-pi.txt
(cd frontend && npm ci && npm run build)          # or rsync dist/ from the laptop
sudo systemctl restart strix1
```
