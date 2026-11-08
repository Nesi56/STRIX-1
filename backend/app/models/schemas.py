"""Request/response models (input validation for the REST API)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginRequest(_Strict):
    token: str = Field(min_length=1, max_length=512)


class ServoAngleRequest(_Strict):
    angle: float = Field(ge=0.0, le=180.0, description="Requested camera tilt angle (clamped to safe limits)")


class ServoLimitsRequest(_Strict):
    min_angle: float = Field(ge=0.0, le=180.0)
    max_angle: float = Field(ge=0.0, le=180.0)
    max_speed_dps: float = Field(ge=5.0, le=360.0)
    center_angle: float | None = Field(default=None, ge=0.0, le=180.0)


class CameraConfigRequest(_Strict):
    width: int = Field(ge=320, le=4608)
    height: int = Field(ge=240, le=2592)
    fps: float = Field(ge=1.0, le=120.0)


class CalibrationRequest(_Strict):
    duration_s: float = Field(4.0, ge=1.0, le=30.0)
    level: bool = Field(True, description="Also compute accelerometer level offsets (airframe must be level)")


class AgentRunRequest(_Strict):
    task: str = Field(min_length=1, max_length=1000, description="What Qwen should do or answer")


class PromptsRequest(_Strict):
    prompts: list[str] = Field(max_length=12, description="Detector classes to show, e.g. ['person', 'car']")


class ServoNudgeRequest(_Strict):
    up_deg: float = Field(ge=-90.0, le=90.0, description="Relative camera tilt; + is up")


class ServoSpeedRequest(_Strict):
    max_speed_dps: float = Field(ge=5.0, le=360.0)


class RecordingStartRequest(_Strict):
    name: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=2000)


class RecordingUpdateRequest(_Strict):
    name: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=2000)


class VORequest(_Strict):
    enabled: bool


class WatchRequest(_Strict):
    enabled: bool


class MemoryAimRequest(_Strict):
    target: str = Field(min_length=1, max_length=40, description="Remembered object: class name ('sports ball') or '#id'")


class DemoSceneRequest(_Strict):
    scene: Literal["outdoor", "cups"]
