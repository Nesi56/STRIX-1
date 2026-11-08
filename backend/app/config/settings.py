"""Runtime configuration for STRIX-1.

All settings come from environment variables (optionally loaded from the
repository-level ``.env`` file). Application settings use the ``STRIX_``
prefix; the AI integrations use the names requested in the project spec
(``QWEN_*``).

Secrets (auth token, xAI key) are wrapped in ``SecretStr`` so they are never
printed in logs or returned by the API.
"""

from __future__ import annotations

import ipaddress
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ENV_FILE = REPO_ROOT / ".env"

# Private / link-local / loopback ranges. 100.64.0.0/10 is carrier-grade NAT,
# also used by overlay VPNs such as Tailscale.
DEFAULT_ALLOWED_NETWORKS = (
    "127.0.0.0/8,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,"
    "169.254.0.0/16,fe80::/10,fc00::/7,100.64.0.0/10"
)


def _alias(*names: str) -> AliasChoices:
    return AliasChoices(*names)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="STRIX_",
        env_file=str(DEFAULT_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
        case_sensitive=False,
    )

    # ------------------------------------------------------------------ server
    host: str = "0.0.0.0"
    port: int = Field(8000, ge=1, le=65535)
    log_level: str = "INFO"
    data_dir: Path = REPO_ROOT / "data"
    frontend_dist: Path = REPO_ROOT / "frontend" / "dist"
    # Force every hardware backend into clearly labeled synthetic mode.
    mock: bool = False
    cors_origins: str = ""  # comma separated, only needed for the Vite dev server

    # -------------------------------------------------------------------- auth
    auth_enabled: bool = True
    auth_token: SecretStr | None = None
    session_secret: SecretStr | None = None
    session_ttl_hours: int = Field(72, ge=1, le=24 * 30)
    allowed_networks: str = DEFAULT_ALLOWED_NETWORKS
    # Set to true only if you deliberately expose the API beyond private ranges.
    allow_public_clients: bool = False

    # --------------------------------------------------------------- telemetry
    telemetry_hz: float = Field(20.0, ge=1.0, le=60.0)

    # --------------------------------------------------------------------- IMU
    imu_backend: Literal["auto", "adafruit", "mock", "disabled"] = "auto"
    imu_address: int = 0x68
    imu_rate_hz: float = Field(100.0, ge=5.0, le=500.0)
    imu_accel_range_g: Literal[2, 4, 8, 16] = 4
    imu_gyro_range_dps: Literal[250, 500, 1000, 2000] = 500
    imu_dlpf_hz: Literal[5, 10, 21, 44, 94, 184, 260] = 44
    imu_filter_alpha: float = Field(0.98, gt=0.5, lt=1.0)
    # Remap sensor axes to body axes, e.g. "-y,x,z" if the board is rotated 90°.
    imu_axis_map: str = "x,y,z"
    imu_history_s: int = Field(300, ge=30, le=3600)

    # ------------------------------------------------------------------- DHT11
    dht_backend: Literal["auto", "kernel", "adafruit", "mock", "disabled"] = "auto"
    dht_gpio: int = 4
    dht_interval_s: float = Field(3.0, ge=2.0, le=60.0)  # DHT11: max ~1 Hz, be gentle
    dht_iio_device: str = ""  # e.g. /sys/bus/iio/devices/iio:device0 (auto-detect if empty)

    # ------------------------------------------------------------------- servo
    servo_backend: Literal["auto", "gpiozero", "hardware_pwm", "mock", "disabled"] = "auto"
    servo_gpio: int = 18
    servo_min_angle: float = Field(30.0, ge=0.0, le=180.0)
    servo_max_angle: float = Field(150.0, ge=0.0, le=180.0)
    servo_center_angle: float = Field(90.0, ge=0.0, le=180.0)
    servo_min_pulse_us: int = Field(500, ge=300, le=1500)  # pulse at 0°
    servo_max_pulse_us: int = Field(2400, ge=1500, le=2700)  # pulse at 180°
    servo_max_speed_dps: float = Field(60.0, ge=5.0, le=360.0)
    # Only used with servo_backend="hardware_pwm" (never chosen by "auto").
    servo_pwm_chip: int = 0
    servo_pwm_channel: int = 2  # Pi 5: GPIO18 = RP1 PWM0 channel 2 (verify!)
    servo_detach_when_idle: bool = True
    servo_start_armed: bool = False
    # true: a larger angle tilts the camera UP. Flip if your mount is mirrored.
    servo_up_is_positive: bool = True

    # ------------------------------------------------------------------ camera
    camera_backend: Literal["auto", "picamera2", "mock", "disabled"] = "auto"
    camera_width: int = Field(1280, ge=320, le=4608)
    camera_height: int = Field(720, ge=240, le=2592)
    camera_fps: float = Field(30.0, ge=1.0, le=120.0)
    camera_jpeg_quality: int = Field(80, ge=30, le=95)
    camera_hflip: bool = False
    camera_vflip: bool = False
    camera_autofocus: bool = True
    camera_analysis_width: int = Field(640, ge=160, le=1280)
    camera_record_bitrate: int = Field(8_000_000, ge=500_000, le=50_000_000)
    webrtc_enabled: bool = False
    mediamtx_rtsp_url: str = "rtsp://127.0.0.1:8554/strix"
    mediamtx_whep_url: str = "http://127.0.0.1:8889/strix/whep"

    # --------------------------------------------------------------- recording
    recording_sample_hz: float = Field(10.0, ge=0.5, le=100.0)
    recording_retention_days: int = Field(30, ge=0, le=3650)  # 0 = keep forever
    recording_max_db_mb: int = Field(2048, ge=16)

    # ------------------------------------------------- detector ("YOLO stream")
    # auto: synthetic boxes with the mock camera; ONNX model if one is found; else HOG person detector.
    detector_backend: Literal["auto", "onnx", "hog", "synthetic", "disabled"] = "auto"
    detector_model: str = ""  # empty = first *.onnx in <repo>/models (yolo11*/yolov8*/yolox* preferred)
    detector_format: Literal["auto", "yolov8", "yolox", "yolov5"] = "auto"
    detector_labels: str = ""  # optional labels file (one per line); COCO-80 by default
    detector_conf: float = Field(0.35, ge=0.05, le=0.99)
    detector_iou: float = Field(0.45, ge=0.1, le=0.9)
    detector_imgsz: int = Field(0, ge=0, le=1280)  # 0 = from the model (OpenCV DNN: 640)
    detector_hz: float = Field(3.0, ge=0.5, le=30.0)  # upper bound; inference time limits it
    detector_prompts: str = "person,cell phone,pen,cup,bottle"  # comma separated classes shown on the video
    camera_hfov_deg: float = Field(102.0, ge=30.0, le=180.0)  # Camera Module 3 Wide

    # ------------------------------------------------------------- agent
    agent_max_plan_steps: int = Field(5, ge=1, le=10)
    agent_skill_timeout_s: float = Field(12.0, ge=2.0, le=60.0)
    agent_standoff_m: float = Field(1.5, ge=0.3, le=20.0)  # distance used for approach suggestions
    agent_watch: bool = True  # Qwen comments automatically when YOLO sees new objects
    agent_watch_interval_s: float = Field(45.0, ge=5.0, le=600.0)  # minimum gap between watch comments
    agent_image_px: int = Field(336, ge=224, le=1280)  # width of camera frames sent to a Qwen vision model
    agent_watch_image: bool = False  # automatic comments also send the camera image (slow/hot on the Pi)
    vo_enabled: bool = False

    # --- object memory (seen / remembered / hidden) ---
    memory_ttl_s: float = Field(600.0, ge=10.0, le=86400.0)  # forget objects not seen for this long
    memory_miss_frames: int = Field(3, ge=1, le=30)  # missed detections (in view) before "occluded"/"hidden"
    memory_containers: str = "cup,bowl,vase,wine glass"  # classes that can hide other objects
    # typical-size overrides for distance estimates, e.g. "sports ball=0.067,cup=0.11" (metres)
    detector_size_priors: str = ""
    # synthetic demo scene (STRIX_MOCK=true only): "outdoor" (people/car) or "cups" (shell game)
    mock_scene: Literal["outdoor", "cups"] = "outdoor"

    # -------------------------------------------------------------- local Qwen
    qwen_enabled: bool = Field(True, validation_alias=_alias("QWEN_ENABLED", "qwen_enabled"))
    qwen_server_url: str = Field(
        "http://127.0.0.1:8080", validation_alias=_alias("QWEN_SERVER_URL", "qwen_server_url")
    )
    # "auto": if no Qwen server answers, start llama-server ourselves when a model is installed in models/
    qwen_manage_server: bool | Literal["auto"] = Field(
        "auto", validation_alias=_alias("QWEN_MANAGE_SERVER", "qwen_manage_server")
    )
    qwen_llama_server_bin: str = Field(
        "llama-server", validation_alias=_alias("QWEN_LLAMA_SERVER_BIN", "qwen_llama_server_bin")
    )
    qwen_model_path: str = Field("", validation_alias=_alias("QWEN_MODEL_PATH", "qwen_model_path"))
    models_dir: Path = REPO_ROOT / "models"
    qwen_mmproj_path: str = Field(
        "", validation_alias=_alias("QWEN_MMPROJ_PATH", "qwen_mmproj_path")
    )
    qwen_model_id: str = Field(
        "qwen2.5-3b-instruct-q4_k_m", validation_alias=_alias("QWEN_MODEL_ID", "qwen_model_id")
    )
    qwen_ctx_size: int = Field(
        4096, ge=512, le=32768, validation_alias=_alias("QWEN_CTX_SIZE", "qwen_ctx_size")
    )
    qwen_threads: int = Field(3, ge=1, le=4, validation_alias=_alias("QWEN_THREADS", "qwen_threads"))
    qwen_max_tokens: int = Field(
        512, ge=16, le=4096, validation_alias=_alias("QWEN_MAX_TOKENS", "qwen_max_tokens")
    )
    qwen_temperature: float = Field(
        0.3, ge=0.0, le=2.0, validation_alias=_alias("QWEN_TEMPERATURE", "qwen_temperature")
    )
    qwen_timeout_s: float = Field(
        180.0, ge=5.0, le=1800.0, validation_alias=_alias("QWEN_TIMEOUT_S", "qwen_timeout_s")
    )
    qwen_api_key: SecretStr | None = Field(
        None, validation_alias=_alias("QWEN_API_KEY", "qwen_api_key")
    )

    # ---------------------------------------------------------------- helpers
    @field_validator("imu_address", mode="before")
    @classmethod
    def _parse_address(cls, v: object) -> object:
        # Accept "0x68" as well as "104" from environment files.
        return int(v.strip(), 0) if isinstance(v, str) else v

    @field_validator("imu_accel_range_g", "imu_gyro_range_dps", "imu_dlpf_hz", mode="before")
    @classmethod
    def _coerce_literal_int(cls, v: object) -> object:
        # Environment values are strings; Literal[int] needs real ints.
        return int(v.strip()) if isinstance(v, str) and v.strip().lstrip("-").isdigit() else v

    @field_validator("imu_axis_map")
    @classmethod
    def _validate_axis_map(cls, v: str) -> str:
        parse_axis_map(v)  # raises ValueError on bad input
        return v

    @field_validator("allowed_networks")
    @classmethod
    def _validate_networks(cls, v: str) -> str:
        for part in filter(None, (p.strip() for p in v.split(","))):
            ipaddress.ip_network(part, strict=False)
        return v

    @model_validator(mode="after")
    def _validate_servo(self) -> "Settings":
        if self.servo_min_angle >= self.servo_max_angle:
            raise ValueError("STRIX_SERVO_MIN_ANGLE must be < STRIX_SERVO_MAX_ANGLE")
        if not (self.servo_min_angle <= self.servo_center_angle <= self.servo_max_angle):
            raise ValueError("STRIX_SERVO_CENTER_ANGLE must lie within the safe servo limits")
        return self

    @property
    def networks(self) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
        return [
            ipaddress.ip_network(p.strip(), strict=False)
            for p in self.allowed_networks.split(",")
            if p.strip()
        ]

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def db_path(self) -> Path:
        return self.data_dir / "strix1.sqlite3"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    def backend_for(self, configured: str) -> str:
        """Resolve a configured hardware backend, honouring global mock mode."""
        if configured == "disabled":
            return "disabled"
        return "mock" if self.mock else configured


def parse_axis_map(spec: str) -> list[tuple[int, float]]:
    """Parse an axis map such as ``"-y,x,z"`` into ``[(index, sign), ...]``.

    Each body axis (X, Y, Z) is assigned one signed sensor axis. Every sensor
    axis must be used exactly once.
    """
    parts = [p.strip().lower() for p in spec.split(",")]
    if len(parts) != 3:
        raise ValueError("axis map needs exactly three comma-separated entries, e.g. 'x,y,z'")
    result: list[tuple[int, float]] = []
    used: set[int] = set()
    for p in parts:
        sign = -1.0 if p.startswith("-") else 1.0
        name = p.lstrip("+-")
        if name not in ("x", "y", "z"):
            raise ValueError(f"invalid axis '{p}' in axis map")
        idx = "xyz".index(name)
        if idx in used:
            raise ValueError("each sensor axis may appear only once in the axis map")
        used.add(idx)
        result.append((idx, sign))
    return result


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
