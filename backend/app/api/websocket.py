"""``/ws/telemetry`` — live telemetry, events and clock sync.

Protocol (JSON text frames)
---------------------------
server → client
  {"type": "hello", "server_time", "version", "telemetry_hz", "demo_mode"}
  {"type": "telemetry", "ts", "seq", "imu": {...}, ...; "full": true once per second with all subsystems}
  {"type": "event", "event": {"ts", "level", "source", "message"}}
  {"type": "pong", "client_ts", "server_ts"}
client → server
  {"type": "ping", "client_ts": <ms since epoch>}   (RTT + clock offset for latency estimates)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app import __version__
from app.api.auth import origin_allowed
from app.services.telemetry_service import ConnectionManager, clean_json

log = logging.getLogger(__name__)
ws_router = APIRouter()


@ws_router.websocket("/ws/telemetry")
async def telemetry_ws(ws: WebSocket) -> None:
    app = ws.app
    auth = app.state.auth
    c = app.state.container
    if not origin_allowed(ws, c.settings.cors_origin_list):
        await ws.close(code=1008)
        return
    if not auth.websocket_ok(ws):
        await ws.close(code=4401)
        return
    await ws.accept()
    client = c.telemetry.manager.add(ws)
    await ws.send_text(json.dumps(clean_json({
        "type": "hello", "server_time": time.time(), "version": __version__,
        "telemetry_hz": c.settings.telemetry_hz, "demo_mode": c.telemetry.demo_mode(),
        "snapshot": c.telemetry.snapshot(),
    })))
    sender = asyncio.create_task(ConnectionManager.sender(client))
    try:
        while True:
            raw = await ws.receive_text()
            if len(raw) > 4096:
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(msg, dict) and msg.get("type") == "ping":
                client.offer(json.dumps({"type": "pong", "client_ts": msg.get("client_ts"), "server_ts": time.time() * 1000}))
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # connection reset etc.
        log.debug("websocket closed: %s", exc)
    finally:
        sender.cancel()
        c.telemetry.manager.remove(client)
