"""ASGI middleware that only admits clients from private/local networks.

STRIX-1 is meant for same-network Wi-Fi access. Requests from public IP
addresses are rejected unless ``STRIX_ALLOW_PUBLIC_CLIENTS=true`` — a
deliberate opt-in. This complements (does not replace) a host firewall.
"""

from __future__ import annotations

import ipaddress
import json
import logging

from app.config.settings import Settings

log = logging.getLogger(__name__)


class LocalNetworkGuard:
    def __init__(self, app, settings: Settings) -> None:
        self.app = app
        self.enabled = not settings.allow_public_clients
        self.networks = settings.networks
        self._warned: set[str] = set()

    def allowed(self, host: str | None) -> bool:
        if not self.enabled:
            return True
        if host is None or host in ("testclient", "testserver"):
            return True  # in-process ASGI test client
        try:
            ip = ipaddress.ip_address(host.split("%", 1)[0])
        except ValueError:
            return False
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        return any(ip in n for n in self.networks)

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            client = scope.get("client")
            host = client[0] if client else None
            if not self.allowed(host):
                if host not in self._warned:
                    self._warned.add(host or "?")
                    log.warning("rejected non-local client %s (set STRIX_ALLOW_PUBLIC_CLIENTS to override)", host)
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                    return
                body = json.dumps({"error": {"code": "forbidden_network", "message": "client network not allowed"}}).encode()
                await send({"type": "http.response.start", "status": 403,
                            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)
