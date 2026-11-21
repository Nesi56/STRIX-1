# New sensors for STRIX-1

Ordered by what they add to the project. Every pick fits the Pi 5 and keeps STRIX-1's rules: real readings
only, estimates labelled, and the pilot flies.

**Weight first.** A 250-size frame with a Pi 5 and a power bank is already heavy. Weigh every addition, and
consider a 5-7" frame if you add more than one or two of these.

| # | Sensor | What it adds to STRIX-1 | How it connects | Try it in the simulator first? |
|---|---|---|---|---|
| 1 | **GPS + compass module** (u-blox M10 receiver with a QMC5883L or IST8310 compass) | Real positions, true heading (not "relative yaw"), the hub's map, sharing what each drone saw, the Lattice idea | UART on GPIO14/15 (pins 8 and 10, 3.3 V), or plugged into a flight controller that STRIX-1 reads over MAVLink | Yes: PX4's simulated GPS is already wired in |
| 2 | **Flight-controller telemetry** (PX4, ArduPilot or INAV over MAVLink) | Battery %, voltage and current, GPS, flight mode, armed state, read-only | A spare FC UART to the Pi's UART; `STRIX_MAVLINK_URL=/dev/serial0,921600` | Yes: that's exactly what the simulation uses |
| 3 | **Thermal camera** (FLIR Lepton 3.5 on a PureThermal USB board, or a small USB-C thermal camera such as InfiRay P2 Pro) | Finds people by body heat at night, in shade or under trees: the strongest search-and-rescue feature | USB | Gazebo has a thermal camera sensor (not set up in STRIX-1 yet) |
| 4 | **Distance sensor** (TF-Luna or TFmini-S lidar; VL53L1X time-of-flight for short range) | Measured distance to what the camera centres on, replacing "about 3 m" estimates; pointed down, height above ground | UART or I2C (pins 3 and 5) | Yes: PX4's `x500_lidar_front` / `x500_lidar_down` models |
| 5 | **Better air sensor** (BME280; SHT45 for accuracy) | Replaces the DHT11 (±2 °C, slow, fussy timing on the Pi 5); adds air pressure for altitude changes | I2C, shares the bus with the IMU | No |
| 6 | **Better IMU** (BNO085, or ICM-42688-P) | BNO085 fuses gyro, accelerometer and magnetometer on the chip: absolute heading and steadier angles | I2C or SPI | PX4's simulated IMU already stands in |
| 7 | **AI accelerator** (Raspberry Pi AI HAT+ with Hailo, or the Raspberry Pi AI Camera with on-sensor AI) | YOLO at 30+ fps with far less heat on the Pi | PCIe HAT, or the camera connector | No |
| 8 | **Depth camera** (Luxonis OAK-D Lite) | A measured distance for every detected object, plus on-board AI | USB-C | Yes: PX4's `x500_depth` model carries an OAK-D Lite |
| 9 | **Power monitor** (INA226 or INA260) | Real voltage and current of the Pi's supply for the power budget (battery % still comes only from a flight controller or fuel gauge) | I2C | No |
| 10 | **ADS-B receiver** (uAvionix pingRX, or an RTL-SDR dongle with dump1090) | Warns the pilot about nearby planes and helicopters: an advisory safety feature | USB or UART | No |

## Recommended order

1. **GPS + compass** and **flight-controller telemetry**. Together they turn on everything the simulation
   already shows: real positions, a real map, true heading and battery level.
2. **Thermal camera**. It makes the search-and-rescue story real.
3. **Distance sensor**. It upgrades the "about" distances to measured ones when an object is centred.

## Not a sensor, but required to fly legally

In the US, a drone over 250 g must broadcast **Remote ID** (FAA Part 89) unless it flies at an FAA-recognized
identification area (FRIA). With the Pi and power bank, the ZMR250 is over 250 g, so add a Remote ID broadcast
module. Recreational pilots also need the free TRUST certificate. Check the current FAA rules before flying.
