import struct

import pytest

from app.config.settings import parse_axis_map
from app.core.orientation import STANDARD_GRAVITY as G
from app.services.dht_service import DHTReadError, KernelIIODHT, find_dht_iio_device, parse_iio_milli, validate_dht11
from app.services.imu_service import parse_mpu6050_burst
from app.services.system_monitor import parse_pmic_adc, parse_proc_net_wireless, parse_throttled


def test_mpu6050_burst_scaling():
    # +1 g on Z at ±4 g range (8192 LSB/g), 65.5 LSB per °/s at ±500 °/s, temp raw 0 -> 36.53 °C
    buf = struct.pack(">7h", 0, -8192, 8192, 0, 655, 0, -131)
    accel, gyro, temp = parse_mpu6050_burst(buf, 4, 500)
    assert accel == pytest.approx((0.0, -G, G))
    assert gyro == pytest.approx((10.0, 0.0, -2.0), abs=1e-6)
    assert temp == pytest.approx(36.53)


def test_mpu6050_burst_length_checked():
    with pytest.raises(ValueError):
        parse_mpu6050_burst(b"\x00" * 13, 2, 250)


def test_iio_milli():
    assert parse_iio_milli("23000\n") == 23.0
    assert parse_iio_milli("-1500") == -1.5
    with pytest.raises(DHTReadError):
        parse_iio_milli("garbage")


def test_dht11_plausibility():
    validate_dht11(22.0, 45.0)
    with pytest.raises(DHTReadError):
        validate_dht11(120.0, 45.0)
    with pytest.raises(DHTReadError):
        validate_dht11(22.0, 140.0)


def test_kernel_iio_backend_with_fake_sysfs(tmp_path):
    dev = tmp_path / "iio:device0"
    dev.mkdir()
    (dev / "name").write_text("dht11@4\n")
    (dev / "in_temp_input").write_text("24000\n")
    (dev / "in_humidityrelative_input").write_text("51000\n")
    assert find_dht_iio_device(tmp_path) == dev
    backend = KernelIIODHT(str(dev))
    assert backend.read() == (24.0, 51.0)
    (dev / "in_temp_input").unlink()  # simulates EIO-style failure
    with pytest.raises(DHTReadError):
        backend.read()


def test_find_dht_ignores_other_iio_devices(tmp_path):
    dev = tmp_path / "iio:device0"
    dev.mkdir()
    (dev / "name").write_text("ads1015\n")
    assert find_dht_iio_device(tmp_path) is None


def test_parse_throttled():
    d = parse_throttled("throttled=0x50005\n")
    assert d["under_voltage_now"] and d["throttled_now"]
    assert d["under_voltage_occurred"] and d["throttled_occurred"]
    assert not d["arm_freq_capped_now"]
    assert parse_throttled("throttled=0x0")["under_voltage_now"] is False
    assert parse_throttled("error") is None


PMIC = """\
     3V7_WL_SW_A current(0)=0.10050000A
       3V3_SYS_A current(1)=0.06343000A
      VDD_CORE_A current(7)=2.31000000A
     3V7_WL_SW_V volt(8)=3.69548000V
       3V3_SYS_V volt(9)=3.31233000V
      VDD_CORE_V volt(15)=0.87000000V
         EXT5V_V volt(24)=5.10208000V
"""


def test_parse_pmic_adc():
    d = parse_pmic_adc(PMIC)
    assert d["input_voltage_v"] == pytest.approx(5.10208)
    expected = 0.1005 * 3.69548 + 0.06343 * 3.31233 + 2.31 * 0.87
    assert d["rail_power_w"] == pytest.approx(expected, abs=1e-3)
    assert parse_pmic_adc("") is None


def test_parse_proc_net_wireless():
    text = """Inter-| sta-|   Quality        |   Discarded packets               | Missed | WE
 face | tus | link level noise |  nwid  crypt   frag  retry   misc | beacon | 22
 wlan0: 0000   58.  -52.  -256        0      0      0      0     12        0
"""
    d = parse_proc_net_wireless(text)
    assert d == {"wlan0": {"link_quality": 58.0, "signal_dbm": -52.0}}


def test_axis_map():
    assert parse_axis_map("x,y,z") == [(0, 1.0), (1, 1.0), (2, 1.0)]
    assert parse_axis_map("-y, x, z") == [(1, -1.0), (0, 1.0), (2, 1.0)]
    for bad in ("x,y", "x,x,z", "x,y,w"):
        with pytest.raises(ValueError):
            parse_axis_map(bad)


def test_env_example_parses(tmp_path):
    """The shipped .env.example (with inline comments) must load cleanly."""
    from app.config.settings import REPO_ROOT, Settings

    s = Settings(_env_file=str(REPO_ROOT / ".env.example"))
    assert s.imu_address == 0x68 and s.imu_accel_range_g == 4 and s.imu_dlpf_hz == 44
    assert s.host == "0.0.0.0" and s.port == 8000 and s.mock is False
    # Empty values parse as empty secrets; services treat them as "not configured".
    assert not (s.auth_token and s.auth_token.get_secret_value().strip())
    assert s.detector_backend == "auto" and s.detector_prompts == "person" and s.servo_up_is_positive is True
    assert s.servo_backend == "auto" and s.qwen_threads == 3
    assert s.qwen_model_path == "" and s.qwen_manage_server == "auto"  # auto-detected from models/


def test_env_strings_are_coerced(tmp_path, monkeypatch):
    from app.config.settings import Settings

    monkeypatch.setenv("STRIX_IMU_ADDRESS", "0x69")
    monkeypatch.setenv("STRIX_IMU_GYRO_RANGE_DPS", "1000")
    s = Settings(_env_file=None)
    assert s.imu_address == 0x69 and s.imu_gyro_range_dps == 1000
    monkeypatch.setenv("STRIX_IMU_GYRO_RANGE_DPS", "300")
    with pytest.raises(Exception):
        Settings(_env_file=None)
