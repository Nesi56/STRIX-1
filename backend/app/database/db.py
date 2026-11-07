"""SQLite storage (WAL mode, single connection guarded by a lock).

Writes from the 10 Hz telemetry recorder are batched by a background writer
thread so the event loop never waits on disk I/O.
"""

from __future__ import annotations

import logging
import queue
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Iterator

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    notes       TEXT,
    started_at  REAL NOT NULL,
    ended_at    REAL,
    sample_hz   REAL,
    synthetic   INTEGER NOT NULL DEFAULT 0,
    metadata    TEXT
);

CREATE TABLE IF NOT EXISTS telemetry (
    id          INTEGER PRIMARY KEY,
    session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    ts          REAL NOT NULL,
    ax REAL, ay REAL, az REAL,
    gx REAL, gy REAL, gz REAL,
    imu_temp REAL,
    roll REAL, pitch REAL, yaw_rel REAL,
    env_temp REAL, humidity REAL, env_ts REAL,
    servo_angle REAL,
    cpu_percent REAL, mem_percent REAL, cpu_temp REAL,
    camera_fps REAL,
    status TEXT
);
CREATE INDEX IF NOT EXISTS idx_telemetry_session_ts ON telemetry(session_id, ts);

CREATE TABLE IF NOT EXISTS observations (
    id          INTEGER PRIMARY KEY,
    session_id  INTEGER REFERENCES sessions(id) ON DELETE SET NULL,
    ts          REAL NOT NULL,
    source      TEXT NOT NULL,
    kind        TEXT NOT NULL,
    data        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_observations_ts ON observations(ts);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY,
    session_id  INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
    ts          REAL NOT NULL,
    level       TEXT NOT NULL,
    source      TEXT NOT NULL,
    message     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);

CREATE TABLE IF NOT EXISTS chat_messages (
    id          INTEGER PRIMARY KEY,
    ts          REAL NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    provider    TEXT,
    model       TEXT,
    meta        TEXT
);

CREATE TABLE IF NOT EXISTS ai_usage (
    id                INTEGER PRIMARY KEY,
    ts                REAL NOT NULL,
    provider          TEXT NOT NULL,
    model             TEXT,
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    cost_usd          REAL,
    latency_ms        REAL,
    ok                INTEGER NOT NULL
);
"""

TELEMETRY_COLUMNS = (
    "session_id", "ts", "ax", "ay", "az", "gx", "gy", "gz", "imu_temp", "roll", "pitch", "yaw_rel",
    "env_temp", "humidity", "env_ts", "servo_angle", "cpu_percent", "mem_percent", "cpu_temp",
    "camera_fps", "status",
)


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None, timeout=10)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            c = self._conn
            c.execute("PRAGMA auto_vacuum=INCREMENTAL")  # effective only on a fresh DB
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.execute("PRAGMA foreign_keys=ON")
            c.executescript(SCHEMA)
            c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
        self._queue: queue.Queue[tuple[str, tuple] | None] = queue.Queue(maxsize=20000)
        self._writer = threading.Thread(target=self._writer_loop, name="sqlite-writer", daemon=True)
        self._writer.start()
        self.dropped_writes = 0

    # ------------------------------------------------------------ writes
    def enqueue(self, sql: str, params: tuple) -> None:
        try:
            self._queue.put_nowait((sql, params))
        except queue.Full:
            self.dropped_writes += 1

    def _writer_loop(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            batch = [item]
            deadline = time.monotonic() + 0.5
            while len(batch) < 2000:
                timeout = deadline - time.monotonic()
                if timeout <= 0:
                    break
                try:
                    nxt = self._queue.get(timeout=timeout)
                except queue.Empty:
                    break
                if nxt is None:
                    self._flush(batch)
                    return
                batch.append(nxt)
            self._flush(batch)

    _FLUSH = "__flush__"

    def _flush(self, batch: list[tuple[str, tuple]]) -> None:
        waiters = [params[0] for sql, params in batch if sql == self._FLUSH]
        work = [(sql, params) for sql, params in batch if sql != self._FLUSH]
        with self._lock:
            if work:
                try:
                    self._conn.execute("BEGIN")
                    for sql, params in work:
                        self._conn.execute(sql, params)
                    self._conn.execute("COMMIT")
                except sqlite3.Error:
                    log.exception("sqlite batch write failed (%d rows)", len(work))
                    try:
                        self._conn.execute("ROLLBACK")
                    except sqlite3.Error:
                        pass
        for ev in waiters:
            ev.set()

    def flush(self, timeout: float = 5.0) -> bool:
        """Block until everything queued so far is committed."""
        ev = threading.Event()
        self._queue.put((self._FLUSH, (ev,)))
        return ev.wait(timeout)

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, tuple(params))

    # ------------------------------------------------------------- reads
    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, tuple(params)).fetchall()]

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        with self._lock:
            r = self._conn.execute(sql, tuple(params)).fetchone()
            return dict(r) if r else None

    def iter_query(self, sql: str, params: Iterable[Any] = (), chunk: int = 5000) -> Iterator[list[dict[str, Any]]]:
        """Yield result rows in chunks without holding the lock between chunks."""
        params = tuple(params)
        offset = 0
        while True:
            rows = self.query(f"{sql} LIMIT ? OFFSET ?", params + (chunk, offset))
            if not rows:
                return
            yield rows
            offset += len(rows)

    def size_bytes(self) -> int:
        total = 0
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(self.path) + suffix)
            if p.exists():
                total += p.stat().st_size
        return total

    def close(self) -> None:
        self._queue.put(None)
        self._writer.join(timeout=5)
        with self._lock:
            try:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
            self._conn.close()
