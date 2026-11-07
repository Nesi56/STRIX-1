"""Logging configuration (console + rotating file in the data directory)."""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path


class RingBufferHandler(logging.Handler):
    """Keeps the last N log records in memory for the dashboard log view."""

    def __init__(self, capacity: int = 500) -> None:
        super().__init__()
        from collections import deque

        self.records: deque[dict] = deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.records.append(
                {
                    "ts": record.created,
                    "level": record.levelname,
                    "logger": record.name,
                    "message": record.getMessage(),
                }
            )
        except Exception:  # pragma: no cover - never let logging crash the app
            pass


ring_handler = RingBufferHandler()


def configure_logging(level: str = "INFO", log_dir: Path | None = None) -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    # Avoid duplicate handlers on reload / repeated app creation in tests.
    for h in list(root.handlers):
        if getattr(h, "_strix", False):
            root.removeHandler(h)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console._strix = True  # type: ignore[attr-defined]
    root.addHandler(console)
    ring_handler._strix = True  # type: ignore[attr-defined]
    ring_handler.setLevel(logging.INFO)
    root.addHandler(ring_handler)
    if log_dir is not None:
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            fh = logging.handlers.RotatingFileHandler(
                log_dir / "strix1.log", maxBytes=5_000_000, backupCount=3
            )
            fh.setFormatter(fmt)
            fh._strix = True  # type: ignore[attr-defined]
            root.addHandler(fh)
        except OSError as exc:  # read-only FS etc.
            logging.getLogger(__name__).warning("file logging disabled: %s", exc)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
