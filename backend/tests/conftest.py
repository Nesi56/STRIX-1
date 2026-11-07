"""Shared fixtures. All tests run in clearly labeled mock (synthetic) mode."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.config.settings import Settings
from app.main import create_app

TOKEN = "test-token-123456"


def make_settings(tmp_path, **overrides) -> Settings:
    base = dict(
        mock=True,
        data_dir=tmp_path / "data",
        frontend_dist=tmp_path / "no-frontend",
        auth_enabled=True,
        auth_token=TOKEN,
        camera_width=640,
        camera_height=360,
        camera_fps=15,
        telemetry_hz=20,
        recording_sample_hz=20,
        qwen_server_url="http://127.0.0.1:9",  # nothing listens here
        grok_enabled=False,
        log_level="WARNING",
    )
    base.update(overrides)
    return Settings(_env_file=None, **base)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def client(settings):
    app = create_app(settings)
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {TOKEN}"
        wait_for(lambda: c.get("/api/imu").json().get("latest") is not None)
        yield c


@pytest.fixture
def anon_client(settings):
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


def wait_for(cond, timeout: float = 5.0, interval: float = 0.05):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            if cond():
                return True
        except Exception:
            pass
        time.sleep(interval)
    raise AssertionError("condition not met within timeout")
