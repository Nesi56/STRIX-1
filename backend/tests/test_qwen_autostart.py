"""QWEN_MANAGE_SERVER=auto: find an installed model and start llama-server only when nothing answers."""

import asyncio

from app.services.ai_qwen_service import QwenService
from app.services.hub import EventLog
from tests.conftest import make_settings


def test_discovers_setup_ai_layout(tmp_path):
    models = tmp_path / "models"
    models.mkdir()
    (models / "Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf").write_bytes(b"GGUF" + b"0" * 2000)
    (models / "mmproj-Qwen2.5-VL-3B-Instruct-Q8_0.gguf").write_bytes(b"GGUF" + b"0" * 1000)
    fake_bin = tmp_path / "llama-server"
    fake_bin.write_text("#!/bin/sh\n")
    fake_bin.chmod(0o755)
    s = make_settings(tmp_path, models_dir=models, qwen_llama_server_bin=str(fake_bin), qwen_manage_server="auto")
    q = QwenService(s, EventLog())
    found = q.discover()
    assert found["model"].endswith("Q4_K_M.gguf") and found["mmproj"].endswith("Q8_0.gguf") and found["bin"] == str(fake_bin)
    assert q.installed is True
    cmd = q.server_command(found)
    assert cmd[0] == str(fake_bin) and "--mmproj" in cmd and "127.0.0.1" in cmd


def test_auto_start_waits_and_reports_not_installed(tmp_path):
    s = make_settings(tmp_path, models_dir=tmp_path / "none", qwen_llama_server_bin="no-such-binary", qwen_manage_server="auto")
    q = QwenService(s, EventLog())
    started = []
    q.start_server = lambda found=None: started.append(found)  # type: ignore[method-assign]
    q.state = "unreachable"
    q._started_at = 0.0  # long ago
    asyncio.run(q._auto_start())
    assert started == [] and "setup_ai.sh" in (q.detail or "")
    assert q.installed is False


def test_auto_start_launches_installed_model(tmp_path):
    models = tmp_path / "models"
    models.mkdir()
    (models / "qwen2.5-1.5b-instruct-q4_k_m.gguf").write_bytes(b"GGUF0000")
    fake_bin = tmp_path / "llama-server"
    fake_bin.write_text("#!/bin/sh\n")
    fake_bin.chmod(0o755)
    s = make_settings(tmp_path, models_dir=models, qwen_llama_server_bin=str(fake_bin), qwen_manage_server="auto")
    q = QwenService(s, EventLog())
    q._other_llama_running = lambda: False  # type: ignore[method-assign]
    started = []
    q.start_server = lambda found=None: started.append(found)  # type: ignore[method-assign]
    q.state = "unreachable"
    q._started_at = 0.0
    asyncio.run(q._auto_start())
    assert started and started[0]["model"].endswith("q4_k_m.gguf") and started[0]["mmproj"] is None
    # explicit false: never starts anything
    s2 = make_settings(tmp_path, models_dir=models, qwen_llama_server_bin=str(fake_bin), qwen_manage_server=False)
    q2 = QwenService(s2, EventLog())
    q2.start_server = lambda found=None: started.append("again")  # type: ignore[method-assign]
    q2.state, q2._started_at = "unreachable", 0.0
    asyncio.run(q2._auto_start())
    assert "again" not in started
