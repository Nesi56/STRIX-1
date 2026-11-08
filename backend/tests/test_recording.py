import csv
import io
import json
import time

from tests.conftest import wait_for


def _record(client, seconds=1.0, name="flight-test"):
    r = client.post("/api/recordings/start", json={"name": name, "notes": "bench"})
    assert r.status_code == 200 and r.json()["active"]
    time.sleep(seconds)
    r = client.post("/api/recordings/stop")
    assert r.status_code == 200
    return r.json()["stopped"]


def test_session_lifecycle_and_export(client):
    sid = _record(client)
    assert client.post("/api/recordings/stop").status_code == 409
    sessions = client.get("/api/recordings").json()["sessions"]
    s = next(x for x in sessions if x["id"] == sid)
    assert s["name"] == "flight-test" and s["synthetic"] is True and s["samples"] >= 10
    assert s["ended_at"] and s["ended_at"] > s["started_at"]

    tel = client.get(f"/api/recordings/{sid}/telemetry?max_points=10").json()
    assert tel["total_samples"] == s["samples"]
    assert len(tel["rows"]) <= 10 + 1
    assert client.get(f"/api/recordings/{sid}/telemetry?max_points=5").status_code == 422
    ts = [r["ts"] for r in tel["rows"]]
    assert ts == sorted(ts)
    row = tel["rows"][0]
    assert row["roll"] is not None and row["status"]["imu"] == "synthetic"

    r = client.get(f"/api/recordings/{sid}/export.csv")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    lines = r.text.splitlines()
    assert lines[0].startswith("# STRIX-1") and "synthetic=yes" in lines[0]
    rows = list(csv.DictReader(io.StringIO("\n".join(lines[1:]))))
    assert len(rows) == s["samples"] and float(rows[0]["ts"]) > 0

    data = json.loads(client.get(f"/api/recordings/{sid}/export.json").text)
    assert data["format"] == "strix1-telemetry" and len(data["telemetry"]) == s["samples"]
    assert data["units"]["gyro"] == "deg/s"
    assert any("session started" in e["message"] for e in data["events"])


def test_cannot_start_twice(client):
    client.post("/api/recordings/start", json={})
    assert client.post("/api/recordings/start", json={}).status_code == 409
    client.post("/api/recordings/stop")


def test_update_and_delete(client):
    sid = _record(client, 0.3)
    r = client.patch(f"/api/recordings/{sid}", json={"name": "renamed", "notes": "n"})
    assert r.json()["name"] == "renamed"
    assert client.delete(f"/api/recordings/{sid}").status_code == 200
    assert client.get(f"/api/recordings/{sid}").status_code == 404
    assert client.get("/api/recordings/9999/export.csv").status_code == 404


def test_retention_removes_old_sessions(client):
    sid = _record(client, 0.3)
    c = client.app.state.container
    c.db.execute("UPDATE sessions SET started_at=started_at-86400*400, ended_at=ended_at-86400*400 WHERE id=?", (sid,))
    res = client.post("/api/recordings/retention").json()
    assert res["removed_by_age"] == 1
    assert client.get(f"/api/recordings/{sid}").status_code == 404


def test_detections_are_logged(client):
    c = client.app.state.container
    wait_for(lambda: c.detector.latest() is not None)
    wait_for(lambda: (c.db.flush() or True) and c.db.query("SELECT COUNT(*) AS n FROM observations")[0]["n"] > 0, timeout=8)
    row = c.db.query("SELECT source, kind, data FROM observations ORDER BY id DESC LIMIT 1")[0]
    assert row["kind"] == "detections" and row["source"] == "synthetic"
