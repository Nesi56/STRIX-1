"""STRIX-1 FastAPI application factory and entry point.

Run (development):   python -m app.main            (from backend/)
Run (production):    see scripts/strix1.service
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api.api import public, router
from app.api.auth import AuthManager
from app.api.network_guard import LocalNetworkGuard
from app.api.websocket import ws_router
from app.config.settings import Settings, get_settings
from app.core.logging_setup import configure_logging
from app.services.container import Container

log = logging.getLogger("strix1")

NOT_BUILT_HTML = """<!doctype html><meta charset=utf-8><title>STRIX-1</title>
<body style="background:#050b16;color:#cfe8ff;font-family:system-ui;padding:2rem">
<h1>STRIX-1 backend is running</h1><p>The dashboard has not been built yet. Run
<code>cd frontend && npm install && npm run build</code>, or use the Vite dev server
(<code>npm run dev</code>) during development.</p><p>API health: <a style="color:#5fd4ff" href="/api/health">/api/health</a></p>"""


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.data_dir / "logs")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        c = app.state.container
        log.info("STRIX-1 %s starting (mock=%s) on %s:%s", __version__, settings.mock, settings.host, settings.port)
        await c.start()
        try:
            yield
        finally:
            log.info("STRIX-1 shutting down")
            await c.stop()

    app = FastAPI(title="STRIX-1 Ground Station", version=__version__, lifespan=lifespan,
                  docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)
    app.state.settings = settings
    app.state.container = container or Container(settings)
    app.state.auth = AuthManager(settings)

    if settings.cors_origin_list:
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=True,
                           allow_methods=["*"], allow_headers=["*"])
    app.add_middleware(LocalNetworkGuard, settings=settings)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        detail = exc.detail if isinstance(exc.detail, dict) else {"code": "http_error", "message": str(exc.detail)}
        return JSONResponse({"error": detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        errors = [{"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
        return JSONResponse({"error": {"code": "validation_error", "message": "invalid request", "details": errors}},
                            status_code=422)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("unhandled error on %s", request.url.path)
        return JSONResponse({"error": {"code": "internal_error", "message": "internal server error"}}, status_code=500)

    app.include_router(public)
    app.include_router(router)
    app.include_router(ws_router)
    _mount_frontend(app, settings.frontend_dist)
    return app


def _mount_frontend(app: FastAPI, dist: Path) -> None:
    index = dist / "index.html"
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        if path.startswith(("api/", "ws/")):
            return JSONResponse({"error": {"code": "not_found", "message": "not found"}}, status_code=404)
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and dist.resolve() in candidate.parents:
            return FileResponse(candidate)
        if index.is_file():
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        return HTMLResponse(NOT_BUILT_HTML)


def main() -> None:
    import uvicorn

    s = get_settings()
    uvicorn.run(create_app(s), host=s.host, port=s.port, log_level=s.log_level.lower(), ws_ping_interval=20,
                ws_ping_timeout=20, proxy_headers=False)


if __name__ == "__main__":
    main()
