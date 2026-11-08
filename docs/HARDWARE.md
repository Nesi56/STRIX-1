# Hardware: wiring, power and interfaces

Code uses **BCM GPIO numbers**; the tables also list **physical header pins**.

```
             3V3  (1) (2)  5V
   SDA  GPIO2  (3) (4)  5V
   SCL  GPIO3  (5) (6)  GND
  DHT11 GPIO4  (7) (8)  GPIO14
             GND  (9) (10) GPIO15
        GPIO17 (11) (12) GPIO18  SG90 signal
        GPIO27 (13) (14) GND
        GPIO22 (15) (16) GPIO23
             3V3 (17) (18) GPIO24
        ...
```

## MPU6050 (Adafruit 6-DoF, I2C)

| MPU6050 | Pi physical pin | BCM |
|---|---|---|
| VIN | 1 (3.3 V) | — |
| GND | 6 | — |
| SDA | 3 | GPIO2 (I2C1 SDA) |
| SCL | 5 | GPIO3 (I2C1 SCL) |

- Address `0x68` (AD0 low/unconnected); `0x69` if AD0 is pulled high → `STRIX_IMU_ADDRESS=0x69`.
- Enable I2C (`raspi-config nonint do_i2c 0`) and check with `i2cdetect -y 1`.
- Driver: Adafruit CircuitPython MPU6050 for configuration (±4 g, ±500 °/s, 44 Hz DLPF by
  default). Samples are read as one 14-byte burst (`ACCEL_XOUT_H … GYRO_ZOUT_L`) through the
  driver's `i2c_device`, so accel, temperature and gyro come from the same instant.
- **Mounting / axes.** The software expects body axes X forward, Y left, Z up (chip side up
  reads ≈ +1 g on Z). If the board is rotated on the frame, remap axes instead of re-soldering,
  e.g. `STRIX_IMU_AXIS_MAP=-y,x,z` for a 90° rotation. Check: tilt the nose up → *pitch* positive;
  lower the right side → *roll* positive.
- **Vibration.** Soft-mount the IMU (foam/gel) and keep the leads short. Motor vibration
  aliases into the accelerometer; the filter's accel-trust gate and the DLPF help, but they do
  not replace mechanical isolation.
- **Limitations.** There is no magnetometer, so there is no absolute heading and yaw drifts.
  Acceleration is never integrated into position.

## DHT11 (temperature / humidity)

| DHT11 | Pi physical pin | BCM |
|---|---|---|
| VCC | 17 (3.3 V) | — |
| DATA | 7 | GPIO4 |
| GND | 9 | — |

- DATA needs a pull-up (4.7–10 kΩ to 3.3 V). Most 3-pin DHT11 *modules* already include one;
  a bare 4-pin sensor does not.
- **Recommended driver on the Pi 5: the kernel IIO driver.** Add `dtoverlay=dht11,gpiopin=4` to
  `/boot/firmware/config.txt` and reboot. Readings then appear under
  `/sys/bus/iio/devices/iio:deviceN/in_temp_input` (m°C) and `in_humidityrelative_input`
  (m%RH). The kernel decodes the timing-critical protocol with interrupts, which user space
  on the Pi 5's RP1 cannot do reliably.
- Fallback: `adafruit_dht` bit-banging (`STRIX_DHT_BACKEND=adafruit`). On the Pi 5 expect frequent
  checksum failures.
- The DHT11 is slow and coarse (≤ 1 Hz, ±2 °C, ±5 %RH). STRIX-1 polls every 3 s, tolerates
  occasional failures and marks the sensor *degraded* or *disconnected* after repeated failures.

## Camera Module 3 Wide (IMX708, 102° HFOV)

- Connect to a Pi 5 CAM/DISP connector with the **Pi 5 (22-pin, narrow) ribbon cable**. The
  Pi 4-style 15-pin cable does not fit without an adapter cable.
- Verify: `rpicam-hello --list-cameras` should list `imx708_wide`.
- Sensor modes (IMX708, typical): 1536×864 up to ~120 fps, 2304×1296 up to ~56 fps,
  4608×2592 up to ~14 fps. STRIX-1 derives the selectable FPS per resolution from the modes
  libcamera reports, so **1080p60 is not offered** (no ≥1080p mode reaches 60 fps). 720p60 is
  available via the 1536×864 mode. `scripts/verify_camera.sh` measures what is actually achieved.
- The Pi 5 has **no hardware video encoder**. Picamera2 uses software JPEG (simplejpeg) for the
  stream and libav/libx264 for H.264 recording. See [STREAMING.md](STREAMING.md#cpu-budget).

## SG90 camera servo

| SG90 wire | Connect to |
|---|---|
| Orange/yellow (signal) | Pi physical pin 12 (GPIO18) |
| Red (+5 V) | 52Pi PD board **OUTPUT +5V** (or another regulated 5 V supply rated ≥ 1 A; SG90 stall ≈ 650–750 mA) |
| Brown/black (GND) | 52Pi PD board **OUTPUT GND**, which must share ground with the Pi |

These are the defaults in the code (`STRIX_SERVO_GPIO=18`), so nothing needs configuring. The SG90 has no
feedback wire, so the Pi **can't detect** whether it's plugged in: the dashboard's SERVO row shows whether the
GPIO18 signal can be driven. Press **Servo on** (under the video) and the ▲/▼ buttons to see it move.
If the row is red, it shows the fix (see [TROUBLESHOOTING.md](TROUBLESHOOTING.md#servo)). The most common
one on the Pi 5 is the systemd service: lgpio needs a writable working directory, so the unit sets
`LG_WD=<repo>/data/lgpio`. Re-run `scripts/install_pi.sh --skip-apt --skip-frontend --systemd` if you
installed the service before this fix.

- **Never power the servo from a GPIO pin.** Avoid the Pi's 5 V header pins too unless the
  supply has headroom: servo current spikes can brown out the Pi (watch for under-voltage
  warnings in the dashboard).
- **52Pi RPi5 PD Power board:** check its board revision and the current rating of its
  regulated 5 V output before using it for the servo. If unsure, use a separate 5 V BEC/UBEC
  from the drone's battery or power bank and tie the grounds together.
- GPIO18 drives the signal at 3.3 V logic, which SG90s accept. A 1 kΩ series resistor in the
  signal line is cheap protection.
- Pulse mapping is configurable (`STRIX_SERVO_MIN_PULSE_US=500`, `STRIX_SERVO_MAX_PULSE_US=2400`
  for 0–180°). SG90 clones vary, so start with narrow safe limits (`STRIX_SERVO_MIN/MAX_ANGLE`)
  and widen them only after checking the mount mechanically.

### Servo PWM backends

| Backend | How | Notes |
|---|---|---|
| `gpiozero` (default, `auto`) | gpiozero `Servo` on the lgpio pin factory | Works out of the box on the Pi 5. Software-timed PWM, so expect slight jitter; STRIX-1 detaches pulses when idle to stop hum. |
| `hardware_pwm` | Kernel PWM via `/sys/class/pwm/pwmchipN/pwmM` | Jitter-free. Needs a PWM overlay and **must be selected explicitly**: `auto` never probes sysfs PWM because a pwmchip on the Pi 5 may belong to the cooling-fan controller. |

Enabling hardware PWM on the Pi 5 (verify each step on your kernel/firmware):

1. Add one overlay to `/boot/firmware/config.txt` and reboot:
   - `dtoverlay=pwm-pio,gpio=18` — Pi 5 PIO-based PWM on any GPIO, or
   - a `pwm`/`pwm-2chan` overlay routing GPIO18 to RP1 PWM0 channel 2 (see
     `/boot/firmware/overlays/README` for the parameters valid on your firmware).
2. Identify the controller and channel:
   ```bash
   ls /sys/class/pwm/                         # pwmchip0, pwmchip1, …
   cat /sys/class/pwm/pwmchip*/npwm           # channel count per chip
   ls -l /sys/class/pwm/                      # the symlink target shows which device each chip is
   pinctrl get 18                             # must show a PWM / PIO function, not "input"
   ```
3. Set `STRIX_SERVO_BACKEND=hardware_pwm`, `STRIX_SERVO_PWM_CHIP=<N>`,
   `STRIX_SERVO_PWM_CHANNEL=<M>`, then test with
   `scripts/verify_servo.py --backend hardware_pwm --chip N --channel M`.
4. The user needs write access to `/sys/class/pwm` (Raspberry Pi OS udev rules grant it to the
   `gpio` group).

## Power

- Pi 5 with a camera, IMU, DHT11, Wi-Fi and a busy LLM can draw 8–12 W. The Anker Nano
  10 000 mAh PD bank and the 52Pi PD board must negotiate **5 V / 5 A** to avoid the Pi 5's
  USB current limit and under-voltage. Check with `scripts/verify_power.sh`:
  - `get_throttled` must stay `0x0` under load (bit 0/16 = under-voltage)
  - `pmic_read_adc EXT5V_V` shows the 5 V input voltage
- The dashboard reports throttling, under-voltage now/since boot, the PMIC 5 V input and an
  *SoC-rail* power estimate. **Battery percentage is not shown**: neither the power bank nor
  the PD board gives the Pi a state-of-charge reading, and an estimate from capacity and time
  would be fiction. To add real battery telemetry, wire an INA219/INA226 on I2C and implement
  a power source in `system_monitor.py`.
