import time

from app.api.network_guard import LocalNetworkGuard
from tests.conftest import TOKEN, make_settings, wait_for


def test_health_is_public(anon_client):
    r = anon_client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["demo_mode"] is True


def test_protected_endpoints_require_auth(anon_client):
    for path in ("/api/sensors", "/api/imu", "/api/system", "/api/camera/status", "/api/servo", "/api/recordings"):
        r = anon_client.get(path)
        assert r.status_code == 401, path
        assert r.json()["error"]["code"] == "unauthorized"


def test_login_sets_httponly_cookie(anon_client):
    assert anon_client.post("/api/auth/login", json={"token": "wrong"}).status_code == 401
    r = anon_client.post("/api/auth/login", json={"token": TOKEN})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert anon_client.get("/api/sensors").status_code == 200
    assert anon_client.get("/api/auth/status").json()["authenticated"] is True
    anon_client.post("/api/auth/logout")
    anon_client.cookies.clear()
    assert anon_client.get("/api/sensors").status_code == 401


def test_tampered_cookie_rejected(anon_client):
    anon_client.post("/api/auth/login", json={"token": TOKEN})
    value = anon_client.cookies.get("strix_session")
    anon_client.cookies.clear()
    anon_client.cookies.set("strix_session", value[:-2] + ("00" if not value.endswith("00") else "11"))
    assert anon_client.get("/api/sensors").status_code == 401


def test_login_rate_limited(anon_client):
    codes = [anon_client.post("/api/auth/login", json={"token": "nope"}).status_code for _ in range(12)]
    assert 429 in codes


def test_sensors_payload_marks_synthetic(client):
    body = client.get("/api/sensors").json()
    assert body["demo_mode"] is True
    assert body["imu"]["status"]["synthetic"] is True
    for dev in body["devices"].values():
        assert dev["synthetic"] is True


def test_validation_errors_are_structured(client):
    r = client.post("/api/servo/angle", json={"angle": 999})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"
    assert client.post("/api/servo/angle", json={"angle": 90, "extra": 1}).status_code == 422
    assert client.post("/api/camera/config", json={"width": 10, "height": 10, "fps": 30}).status_code == 422
    assert client.post("/api/ai/chat", json={"message": ""}).status_code == 422


def test_servo_flow(client):
    assert client.post("/api/servo/angle", json={"angle": 100}).status_code == 409
    assert client.post("/api/servo/arm").json()["armed"] is True
    st = client.post("/api/servo/angle", json={"angle": 179}).json()
    assert st["clamped"] is True and st["target_angle"] == 150
    assert client.post("/api/servo/center").json()["target_angle"] == 90
    assert client.post("/api/servo/disarm").json()["armed"] is False


def test_camera_snapshot_and_traversal(client):
    wait_for(lambda: client.get("/api/camera/status").json()["frame_seq"] > 0)
    snap = client.post("/api/camera/snapshot").json()
    assert snap["synthetic"] is True and "path" not in snap
    r = client.get(f"/api/camera/snapshots/{snap['id']}")
    assert r.status_code == 200 and r.content[:2] == b"\xff\xd8"
    assert client.get("/api/camera/snapshots/..%2F..%2Fstrix1.sqlite3").status_code == 404
    assert client.get("/api/camera/snapshots/.hidden").status_code == 404


def test_camera_recording_lifecycle(client):
    assert client.post("/api/camera/recording/stop").status_code == 409
    r = client.post("/api/camera/recording/start").json()
    assert r["active"] is True
    assert client.post("/api/camera/recording/start").status_code == 409
    time.sleep(0.8)
    r = client.post("/api/camera/recording/stop").json()
    assert r["active"] is False and r["bytes"] and r["bytes"] > 0
    vids = client.get("/api/camera/videos").json()
    assert vids and vids[0]["synthetic"] is True


def test_camera_mode_validation(client):
    r = client.post("/api/camera/config", json={"width": 1920, "height": 1080, "fps": 60})
    assert r.status_code == 409  # 1080p60 not offered by any (mock IMX708-like) sensor mode
    r = client.post("/api/camera/config", json={"width": 640, "height": 360, "fps": 30})
    assert r.status_code == 200 and r.json()["target_fps"] == 30


def test_mjpeg_stream_headers(client):
    wait_for(lambda: client.get("/api/camera/status").json()["frame_seq"] > 0)
    with client.stream("GET", "/api/camera/stream.mjpg?fps=10") as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("multipart/x-mixed-replace")
        chunk = b""
        for part in r.iter_bytes():
            chunk += part
            if b"X-Capture-Time" in chunk and b"\xff\xd8" in chunk:
                break
        assert b"X-Synthetic: 1" in chunk


def test_mapping_position_unavailable(client):
    st = client.get("/api/mapping").json()
    assert st["position"]["available"] is False
    assert st["position"]["message"] == "Position unavailable — no validated localization source."
    vo = [s for s in st["sources"] if s["id"] == "visual_odometry"][0]
    assert vo["validated"] is False and vo["experimental"] is True


def test_config_contains_no_secrets(client):
    text = client.get("/api/config").text
    assert TOKEN not in text


def test_network_guard(tmp_path):
    g = LocalNetworkGuard(None, make_settings(tmp_path))
    for ok in ("127.0.0.1", "192.168.1.20", "10.1.2.3", "172.20.0.5", "::1", "fe80::1", "::ffff:192.168.0.7"):
        assert g.allowed(ok), ok
    for bad in ("8.8.8.8", "1.2.3.4", "2001:4860:4860::8888", "not-an-ip"):
        assert not g.allowed(bad), bad
    g2 = LocalNetworkGuard(None, make_settings(tmp_path, allow_public_clients=True))
    assert g2.allowed("8.8.8.8")


def test_unknown_api_path_404(client):
    assert client.get("/api/does-not-exist").status_code == 404


def test_spa_fallback_without_build(client):
    r = client.get("/")
    assert r.status_code == 200 and "STRIX-1" in r.text


def test_imu_calibration_flow_in_demo_mode(client):
    r = client.post("/api/imu/calibrate", json={"duration_s": 1.5, "level": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "calibrated"
    bias = body["calibration"]["gyro_bias_dps"]
    assert abs(bias[0] - 0.6) < 0.1 and abs(bias[1] + 0.4) < 0.1  # synthetic bias recovered
    assert client.get("/api/imu").json()["calibration"]["state"] == "calibrated"
    assert client.delete("/api/imu/calibration").json()["state"] == "uncalibrated"
    assert client.post("/api/imu/calibrate", json={"duration_s": 0.1}).status_code == 422
