import json
import time

import pytest
from starlette.websockets import WebSocketDisconnect

from tests.conftest import TOKEN


def test_ws_requires_auth(anon_client):
    with pytest.raises(WebSocketDisconnect) as exc:
        with anon_client.websocket_connect("/ws/telemetry") as ws:
            ws.receive_text()
    assert exc.value.code == 4401


def test_ws_rejects_cross_origin(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/telemetry", headers={"origin": "http://evil.example"}) as ws:
            ws.receive_text()


def test_ws_hello_telemetry_and_pong(client):
    with client.websocket_connect("/ws/telemetry") as ws:
        hello = json.loads(ws.receive_text())
        assert hello["type"] == "hello" and hello["demo_mode"] is True
        assert "imu" in hello["snapshot"]
        t0 = time.time() * 1000
        ws.send_text(json.dumps({"type": "ping", "client_ts": t0}))
        seen_full = seen_fast = seen_pong = False
        for _ in range(60):
            m = json.loads(ws.receive_text())
            if m["type"] == "pong":
                assert m["client_ts"] == t0 and m["server_ts"] >= t0 - 1000
                seen_pong = True
            elif m["type"] == "telemetry":
                assert m["imu"]["latest"]["orientation"]["roll"] is not None
                if m.get("full"):
                    seen_full = True
                    assert {"environment", "servo", "camera", "system", "ai", "mapping", "recording"} <= m.keys()
                else:
                    seen_fast = True
            if seen_full and seen_fast and seen_pong:
                break
        assert seen_full and seen_fast and seen_pong


def test_ws_query_token_for_non_browser_clients(anon_client):
    with anon_client.websocket_connect(f"/ws/telemetry?token={TOKEN}") as ws:
        assert json.loads(ws.receive_text())["type"] == "hello"


def test_ws_ignores_garbage(client):
    with client.websocket_connect("/ws/telemetry") as ws:
        ws.receive_text()
        ws.send_text("not json")
        ws.send_text("x" * 10000)
        m = json.loads(ws.receive_text())
        assert m["type"] in ("telemetry", "event")
