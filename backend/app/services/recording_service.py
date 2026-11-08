"""Telemetry recording sessions, history playback, export and retention."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import time
from typing import Any, AsyncIterator, Callable

from app.config.settings import Settings
from app.database.db import TELEMETRY_COLUMNS, Database
from app.services.hub import EventLog

log = logging.getLogger(__name__)

EXPORT_COLUMNS = [c for c in TELEMETRY_COLUMNS if c != "session_id"]


class RecordingError(RuntimeError):
    pass


def row_from_snapshot(session_id: int, snap: dict[str, Any]) -> tuple:
    """Flatten a telemetry snapshot into a telemetry table row."""
    imu = (snap.get("imu") or {}).get("latest") or {}
    acc = imu.get("accel") or {}
    gyr = imu.get("gyro") or {}
    ori = imu.get("orientation") or {}
    env = (snap.get("environment") or {}).get("latest") or {}
    servo = snap.get("servo") or {}
    system = snap.get("system") or {}
    cam = snap.get("camera") or {}
    status = {
        k: (snap.get(k) or {}).get("status", {}).get("state")
        for k in ("imu", "environment", "servo", "camera")
    }
    return (
        session_id,
        snap.get("ts", time.time()),
        acc.get("x"), acc.get("y"), acc.get("z"),
        gyr.get("x"), gyr.get("y"), gyr.get("z"),
        imu.get("temp_c"),
        ori.get("roll"), ori.get("pitch"), ori.get("yaw_relative"),
        env.get("temperature_c"), env.get("humidity_pct"), env.get("ts"),
        servo.get("commanded_angle"),
        system.get("cpu_percent"), (system.get("memory") or {}).get("used_percent"), system.get("cpu_temp_c"),
        cam.get("measured_fps"),
        json.dumps(status, separators=(",", ":")),
    )


class RecordingService:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        events: EventLog,
        snapshot: Callable[[], dict[str, Any]],
        synthetic: Callable[[], bool],
    ) -> None:
        self.settings = settings
        self.db = db
        self.events = events
        self._snapshot = snapshot
        self._synthetic = synthetic
        self.active_session: dict[str, Any] | None = None
        self._task: asyncio.Task | None = None
        self._retention_task: asyncio.Task | None = None
        self._rows = 0
        insert_cols = ",".join(TELEMETRY_COLUMNS)
        self._insert_sql = f"INSERT INTO telemetry({insert_cols}) VALUES ({','.join('?' * len(TELEMETRY_COLUMNS))})"
        events.listeners.append(self._on_event)

    # ---------------------------------------------------------- lifecycle
    async def start(self) -> None:
        # Close sessions left open by a crash / power loss.
        self.db.execute("UPDATE sessions SET ended_at = (SELECT MAX(ts) FROM telemetry t WHERE t.session_id = sessions.id) "
                        "WHERE ended_at IS NULL")
        self.db.execute("UPDATE sessions SET ended_at = started_at WHERE ended_at IS NULL")
        self._retention_task = asyncio.create_task(self._retention_loop(), name="retention")

    async def stop(self) -> None:
        if self.active_session:
            await self.stop_session()
        if self._retention_task:
            self._retention_task.cancel()

    def _on_event(self, ev: dict[str, Any]) -> None:
        sid = self.active_session["id"] if self.active_session else None
        self.db.enqueue(
            "INSERT INTO events(session_id, ts, level, source, message) VALUES (?,?,?,?,?)",
            (sid, ev["ts"], ev["level"], ev["source"], ev["message"]),
        )

    # ------------------------------------------------------------ sessions
    async def start_session(self, name: str | None = None, notes: str | None = None,
                            metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.active_session:
            raise RecordingError("a recording session is already active")
        now = time.time()
        name = (name or "").strip() or time.strftime("Session %Y-%m-%d %H:%M:%S", time.localtime(now))
        synthetic = self._synthetic()
        cur = self.db.execute(
            "INSERT INTO sessions(name, notes, started_at, sample_hz, synthetic, metadata) VALUES (?,?,?,?,?,?)",
            (name, notes, now, self.settings.recording_sample_hz, int(synthetic), json.dumps(metadata or {})),
        )
        self.active_session = {"id": cur.lastrowid, "name": name, "started_at": now, "synthetic": synthetic}
        self._rows = 0
        self._task = asyncio.create_task(self._sampler(cur.lastrowid), name="telemetry-recorder")
        self.events.add("info", "recording", f"telemetry session started: {name}")
        return self.status()

    async def stop_session(self) -> dict[str, Any]:
        if not self.active_session:
            raise RecordingError("no active recording session")
        sid = self.active_session["id"]
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await asyncio.to_thread(self.db.flush)
        self.db.execute("UPDATE sessions SET ended_at=? WHERE id=?", (time.time(), sid))
        name = self.active_session["name"]
        self.active_session = None
        self.events.add("info", "recording", f"telemetry session stopped: {name}")
        return {"stopped": sid, **self.status()}

    async def _sampler(self, session_id: int) -> None:
        period = 1.0 / self.settings.recording_sample_hz
        loop = asyncio.get_running_loop()
        next_t = loop.time()
        while True:
            try:
                snap = self._snapshot()
                self.db.enqueue(self._insert_sql, row_from_snapshot(session_id, snap))
                self._rows += 1
            except Exception:
                log.exception("recording sample failed")
            next_t += period
            await asyncio.sleep(max(0.0, next_t - loop.time()))

    def status(self) -> dict[str, Any]:
        s = self.active_session
        return {
            "active": s is not None,
            "session": None if s is None else {**s, "duration_s": time.time() - s["started_at"], "rows": self._rows},
            "sample_hz": self.settings.recording_sample_hz,
            "db_bytes": self.db.size_bytes(),
            "dropped_writes": self.db.dropped_writes,
            "retention_days": self.settings.recording_retention_days,
        }

    def list_sessions(self) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT s.*, (SELECT COUNT(*) FROM telemetry t WHERE t.session_id = s.id) AS samples "
            "FROM sessions s ORDER BY s.started_at DESC"
        )
        for r in rows:
            r["metadata"] = json.loads(r["metadata"] or "{}")
            r["synthetic"] = bool(r["synthetic"])
            end = r["ended_at"] or time.time()
            r["duration_s"] = end - r["started_at"]
            r["active"] = bool(self.active_session and self.active_session["id"] == r["id"])
        return rows

    def get_session(self, session_id: int) -> dict[str, Any]:
        for s in self.list_sessions():
            if s["id"] == session_id:
                return s
        raise RecordingError(f"session {session_id} not found")

    def update_session(self, session_id: int, name: str | None, notes: str | None) -> dict[str, Any]:
        self.get_session(session_id)
        if name is not None:
            self.db.execute("UPDATE sessions SET name=? WHERE id=?", (name.strip() or "Untitled", session_id))
        if notes is not None:
            self.db.execute("UPDATE sessions SET notes=? WHERE id=?", (notes, session_id))
        return self.get_session(session_id)

    def set_metadata(self, session_id: int, key: str, value: Any) -> None:
        row = self.db.query_one("SELECT metadata FROM sessions WHERE id=?", (session_id,))
        if row is None:
            raise RecordingError(f"session {session_id} not found")
        meta = json.loads(row["metadata"] or "{}")
        meta[key] = value
        self.db.execute("UPDATE sessions SET metadata=? WHERE id=?", (json.dumps(meta), session_id))

    def delete_session(self, session_id: int) -> None:
        if self.active_session and self.active_session["id"] == session_id:
            raise RecordingError("stop the session before deleting it")
        self.get_session(session_id)
        self.db.execute("DELETE FROM telemetry WHERE session_id=?", (session_id,))
        self.db.execute("DELETE FROM events WHERE session_id=?", (session_id,))
        self.db.execute("DELETE FROM sessions WHERE id=?", (session_id,))

    def telemetry(self, session_id: int, start: float | None = None, end: float | None = None,
                  max_points: int = 2000) -> dict[str, Any]:
        """Downsampled telemetry for playback charts."""
        self.get_session(session_id)
        self.db.flush(2.0)
        where = "session_id=?"
        params: list[Any] = [session_id]
        if start is not None:
            where += " AND ts>=?"
            params.append(start)
        if end is not None:
            where += " AND ts<=?"
            params.append(end)
        total = self.db.query_one(f"SELECT COUNT(*) AS n FROM telemetry WHERE {where}", params)["n"]
        stride = max(1, -(-total // max_points))
        cols = ",".join(EXPORT_COLUMNS)
        rows = self.db.query(
            f"SELECT {cols} FROM (SELECT *, ROW_NUMBER() OVER (ORDER BY ts) AS rn FROM telemetry WHERE {where}) "
            f"WHERE (rn - 1) % ? = 0 ORDER BY ts",
            params + [stride],
        )
        for r in rows:
            r["status"] = json.loads(r["status"] or "{}")
        events = self.db.query("SELECT ts, level, source, message FROM events WHERE session_id=? ORDER BY ts", (session_id,))
        return {"session_id": session_id, "total_samples": total, "stride": stride, "rows": rows, "events": events}

    # -------------------------------------------------------------- export
    async def export_csv(self, session_id: int) -> AsyncIterator[bytes]:
        session = self.get_session(session_id)
        await asyncio.to_thread(self.db.flush)
        buf = io.StringIO()
        w = csv.writer(buf)
        buf.write(f"# STRIX-1 telemetry export; session={session['id']}; name={session['name']}; "
                  f"synthetic={'yes' if session['synthetic'] else 'no'}; timestamps=unix seconds UTC\n")
        w.writerow(EXPORT_COLUMNS)
        yield buf.getvalue().encode()
        cols = ",".join(EXPORT_COLUMNS)
        for chunk in self.db.iter_query(f"SELECT {cols} FROM telemetry WHERE session_id=? ORDER BY ts", (session_id,)):
            buf = io.StringIO()
            w = csv.writer(buf)
            for r in chunk:
                w.writerow([r[c] for c in EXPORT_COLUMNS])
            yield buf.getvalue().encode()
            await asyncio.sleep(0)

    async def export_json(self, session_id: int) -> AsyncIterator[bytes]:
        session = self.get_session(session_id)
        await asyncio.to_thread(self.db.flush)
        header = {"format": "strix1-telemetry", "version": 1, "session": session, "units": {
            "accel": "m/s^2", "gyro": "deg/s", "angles": "deg (yaw relative, drifts)", "temperature": "degC",
            "humidity": "%RH", "ts": "unix seconds"}}
        head = json.dumps(header)[:-1] + ', "telemetry": ['
        yield head.encode()
        first = True
        cols = ",".join(EXPORT_COLUMNS)
        for chunk in self.db.iter_query(f"SELECT {cols} FROM telemetry WHERE session_id=? ORDER BY ts", (session_id,)):
            parts = []
            for r in chunk:
                r["status"] = json.loads(r["status"] or "{}")
                parts.append(json.dumps(r))
            yield ((("" if first else ",") + ",".join(parts))).encode()
            first = False
            await asyncio.sleep(0)
        events = self.db.query("SELECT ts, level, source, message FROM events WHERE session_id=? ORDER BY ts", (session_id,))
        yield ('], "events": ' + json.dumps(events) + "}").encode()

    # ------------------------------------------------------------ retention
    def apply_retention(self) -> dict[str, int]:
        removed_age = removed_size = 0
        days = self.settings.recording_retention_days
        active_id = self.active_session["id"] if self.active_session else -1
        if days > 0:
            cutoff = time.time() - days * 86400
            for s in self.db.query("SELECT id FROM sessions WHERE ended_at IS NOT NULL AND ended_at < ? AND id != ?",
                                   (cutoff, active_id)):
                self.delete_session(s["id"])
                removed_age += 1
            self.db.execute("DELETE FROM observations WHERE ts < ?", (cutoff,))
            self.db.execute("DELETE FROM events WHERE session_id IS NULL AND ts < ?", (cutoff,))
        limit = self.settings.recording_max_db_mb * 1_000_000
        while self.db.size_bytes() > limit:
            oldest = self.db.query_one("SELECT id FROM sessions WHERE id != ? ORDER BY started_at LIMIT 1", (active_id,))
            if not oldest:
                break
            self.delete_session(oldest["id"])
            removed_size += 1
            self.db.execute("PRAGMA incremental_vacuum")
            self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        if removed_age or removed_size:
            self.events.add("info", "recording", f"retention removed {removed_age + removed_size} old session(s)")
        return {"removed_by_age": removed_age, "removed_by_size": removed_size}

    async def _retention_loop(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.apply_retention)
            except Exception:
                log.exception("retention pass failed")
            await asyncio.sleep(3600)
