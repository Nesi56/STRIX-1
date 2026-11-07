import { Database, Download, FileJson, FileSpreadsheet, Pause, Play, Square, Trash2, Video } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTelemetry } from "../../hooks/useTelemetry";
import { usePoll } from "../../hooks/usePoll";
import { del, errorMessage, get, post } from "../../services/api";
import { fmt, fmtBytes, fmtDuration, fmtTime } from "../../lib/format";
import type { MediaFile, Session, TelemetryEvent, TelemetryRow } from "../../types/telemetry";
import { Empty, Panel, Tag } from "../common/ui";
import { useToast } from "../common/toast";
import { LiveChart } from "../telemetry/LiveChart";

interface RecordingsResponse { sessions: Session[]; videos: MediaFile[]; snapshots: MediaFile[] }
interface PlaybackData { session_id: number; total_samples: number; stride: number; rows: TelemetryRow[]; events: TelemetryEvent[] }

export function SessionControl() {
  const st = useTelemetry();
  const rec = st?.recording;
  const toast = useToast();
  const [name, setName] = useState("");
  const [notes, setNotes] = useState("");
  const start = async () => {
    try {
      await post("/api/recordings/start", { name: name || null, notes: notes || null });
      toast("Telemetry recording started", "ok");
      setName("");
      setNotes("");
    } catch (e) {
      toast(errorMessage(e), "error");
    }
  };
  const stop = async () => {
    try {
      await post("/api/recordings/stop");
      toast("Telemetry session saved", "ok");
    } catch (e) {
      toast(errorMessage(e), "error");
    }
  };
  return rec?.active && rec.session ? (
    <div className="flex flex-wrap items-center gap-2">
      <Tag tone="crit"><span className="blink">●</span> RECORDING</Tag>
      <span className="font-medium">{rec.session.name}</span>
      <span className="num text-muted">{fmtDuration(rec.session.duration_s)} · {rec.session.rows} samples @ {rec.sample_hz} Hz</span>
      {rec.session.synthetic && <Tag tone="synth">SYNTHETIC</Tag>}
      <button className="btn btn-danger btn-sm ml-auto" onClick={stop}><Square size={11} /> Stop session</button>
    </div>
  ) : (
    <div className="flex flex-wrap items-center gap-2">
      <input className="input w-44" placeholder="Session name" value={name} maxLength={120} onChange={(e) => setName(e.target.value)} />
      <input className="input min-w-0 flex-1" placeholder="Notes (optional)" value={notes} maxLength={2000} onChange={(e) => setNotes(e.target.value)} />
      <button className="btn btn-primary btn-sm" onClick={start}><Play size={11} /> Start session</button>
      <span className="num text-[11px] text-muted">{rec?.sample_hz} Hz · retention {rec?.retention_days ? `${rec.retention_days} d` : "∞"} · DB {fmtBytes(rec?.db_bytes)}</span>
    </div>
  );
}

function Playback({ session, onClose }: { session: Session; onClose: () => void }) {
  const [data, setData] = useState<PlaybackData | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [cursor, setCursor] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    setData(null);
    get<PlaybackData>(`/api/recordings/${session.id}/telemetry?max_points=3000`).then((d) => {
      setData(d);
      setCursor(d.rows[0]?.ts ?? 0);
    }).catch((e) => setErr(errorMessage(e)));
  }, [session.id]);

  const rows = data?.rows ?? [];
  const t0 = rows[0]?.ts ?? 0;
  const t1 = rows[rows.length - 1]?.ts ?? 0;
  useEffect(() => {
    if (!playing) return;
    const step = 0.1;
    timer.current = window.setInterval(() => {
      setCursor((c) => {
        const n = c + step * speed;
        if (n >= t1) {
          setPlaying(false);
          return t1;
        }
        return n;
      });
    }, step * 1000);
    return () => { if (timer.current) clearInterval(timer.current); };
  }, [playing, speed, t1]);

  const at = useMemo(() => {
    if (!rows.length) return null;
    let lo = 0, hi = rows.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (rows[mid].ts < cursor) lo = mid + 1;
      else hi = mid;
    }
    return rows[lo];
  }, [rows, cursor]);
  const rel = (v: number) => fmtDuration(v - t0);

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <b>{session.name}</b>
        {session.synthetic && <Tag tone="synth">SYNTHETIC DATA</Tag>}
        <span className="num text-muted">{fmtTime(session.started_at, true)} · {fmtDuration(session.duration_s)} · {data?.total_samples ?? "…"} samples{data && data.stride > 1 ? ` (showing every ${data.stride})` : ""}</span>
        <button className="btn btn-sm ml-auto" onClick={onClose}>Close</button>
      </div>
      {err && <div className="text-crit">{err}</div>}
      {!data ? <Empty>Loading…</Empty> : rows.length === 0 ? <Empty>No samples in this session.</Empty> : (
        <>
          <div className="flex items-center gap-2">
            <button className="btn btn-sm" onClick={() => { if (cursor >= t1) setCursor(t0); setPlaying((p) => !p); }}>{playing ? <Pause size={11} /> : <Play size={11} />}</button>
            <select className="select !py-0.5" value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>
              {[0.5, 1, 2, 5, 10].map((s) => <option key={s} value={s}>{s}×</option>)}
            </select>
            <input type="range" className="slider" min={t0} max={t1} step={0.05} value={cursor} onChange={(e) => setCursor(Number(e.target.value))} aria-label="Playback position" />
            <span className="num w-24 text-right text-[11px]">{rel(cursor)} / {rel(t1)}</span>
          </div>
          {at && (
            <div className="num grid grid-cols-3 gap-2 rounded border border-line p-2 text-[11px] sm:grid-cols-6">
              <span>{fmtTime(at.ts)}</span>
              <span>roll {fmt(at.roll, 1, "°")}</span><span>pitch {fmt(at.pitch, 1, "°")}</span>
              <span>env {fmt(at.env_temp, 1, "°C")} / {fmt(at.humidity, 0, "%")}</span>
              <span>servo {fmt(at.servo_angle, 0, "°")}</span><span>cpu {fmt(at.cpu_percent, 0, "%")}</span>
            </div>
          )}
          <div className="grid gap-3 lg:grid-cols-2">
            <div><div className="label">Orientation (°)</div>
              <LiveChart data={rows} xKey="ts" unit="°" height={150} xFormatter={rel} referenceX={cursor}
                series={[{ key: "roll", name: "Roll", color: "#38d6ff" }, { key: "pitch", name: "Pitch", color: "#f2b33d" }, { key: "yaw_rel", name: "Rel. yaw", color: "#c08bff" }]} />
            </div>
            <div><div className="label">Acceleration (m/s²)</div>
              <LiveChart data={rows} xKey="ts" unit="m/s²" height={150} xFormatter={rel} referenceX={cursor}
                series={[{ key: "ax", name: "X", color: "#ff5468" }, { key: "ay", name: "Y", color: "#34d58a" }, { key: "az", name: "Z", color: "#4c8dff" }]} />
            </div>
            <div><div className="label">Angular velocity (°/s)</div>
              <LiveChart data={rows} xKey="ts" unit="°/s" height={150} xFormatter={rel} referenceX={cursor}
                series={[{ key: "gx", name: "X", color: "#ff5468" }, { key: "gy", name: "Y", color: "#34d58a" }, { key: "gz", name: "Z", color: "#4c8dff" }]} />
            </div>
            <div><div className="label">Environment & system</div>
              <LiveChart data={rows} xKey="ts" unit="" height={150} xFormatter={rel} referenceX={cursor}
                series={[{ key: "env_temp", name: "Temp °C", color: "#f2b33d" }, { key: "humidity", name: "RH %", color: "#4c8dff" }, { key: "cpu_percent", name: "CPU %", color: "#7d91ab" }]} />
            </div>
          </div>
          {data.events.length > 0 && (
            <div className="max-h-32 overflow-auto rounded border border-line text-[11px]">
              {data.events.map((e, i) => (
                <button key={i} className="flex w-full gap-2 border-b border-line px-2 py-0.5 text-left hover:bg-white/[.03]" onClick={() => setCursor(e.ts)}>
                  <span className="num text-muted">{rel(e.ts)}</span><span className={e.level === "error" ? "text-crit" : e.level === "warning" ? "text-warn" : ""}>[{e.source}] {e.message}</span>
                </button>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

export function RecordingsPanel({ className }: { className?: string }) {
  const toast = useToast();
  const { data, refresh } = usePoll<RecordingsResponse>("/api/recordings", 5000);
  const [selected, setSelected] = useState<Session | null>(null);
  const remove = async (s: Session) => {
    if (!window.confirm(`Delete session "${s.name}" and its ${s.samples} samples?`)) return;
    try {
      await del(`/api/recordings/${s.id}`);
      if (selected?.id === s.id) setSelected(null);
      await refresh();
    } catch (e) {
      toast(errorMessage(e), "error");
    }
  };
  const removeMedia = async (kind: "videos" | "snapshots", name: string) => {
    if (!window.confirm(`Delete ${name}?`)) return;
    try {
      await del(`/api/camera/${kind}/${encodeURIComponent(name)}`);
      await refresh();
    } catch (e) {
      toast(errorMessage(e), "error");
    }
  };
  return (
    <div className={`grid gap-3 ${className ?? ""}`}>
      <Panel title="Telemetry Recording" icon={<Database size={14} />}>
        <SessionControl />
      </Panel>
      {selected && <Panel title="Session Playback" icon={<Play size={14} />}><Playback session={selected} onClose={() => setSelected(null)} /></Panel>}
      <Panel title="Recorded Sessions" icon={<Database size={14} />} bodyClassName="!p-0">
        {!data?.sessions.length ? <Empty>No sessions recorded yet.</Empty> : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-[12px]">
              <thead><tr className="label border-b border-line text-left"><th className="px-3 py-1.5">Name</th><th>Started</th><th>Duration</th><th>Samples</th><th>Data</th><th className="pr-3 text-right">Actions</th></tr></thead>
              <tbody>
                {data.sessions.map((s) => (
                  <tr key={s.id} className={`border-b border-line hover:bg-white/[.02] ${selected?.id === s.id ? "bg-cyan/5" : ""}`}>
                    <td className="px-3 py-1.5"><div>{s.name}</div>{s.notes && <div className="text-[10.5px] text-muted">{s.notes}</div>}</td>
                    <td className="num">{fmtTime(s.started_at, true)}</td>
                    <td className="num">{fmtDuration(s.duration_s)}</td>
                    <td className="num">{s.samples}</td>
                    <td>{s.active ? <Tag tone="crit">ACTIVE</Tag> : s.synthetic ? <Tag tone="synth">SYNTHETIC</Tag> : <Tag tone="ok">HARDWARE</Tag>}</td>
                    <td className="pr-3">
                      <div className="flex justify-end gap-1">
                        <button className="btn btn-sm" disabled={s.active} onClick={() => setSelected(s)}><Play size={11} /> Play</button>
                        <a className="btn btn-sm" href={`/api/recordings/${s.id}/export.csv`} download><FileSpreadsheet size={11} /> CSV</a>
                        <a className="btn btn-sm" href={`/api/recordings/${s.id}/export.json`} download><FileJson size={11} /> JSON</a>
                        <button className="btn btn-sm btn-danger" disabled={s.active} onClick={() => void remove(s)}><Trash2 size={11} /></button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
      <div className="grid gap-3 lg:grid-cols-2">
        <Panel title="Video Recordings" icon={<Video size={14} />} bodyClassName="!p-0">
          {!data?.videos.length ? <Empty>No videos.</Empty> : data.videos.map((v) => (
            <div key={v.name} className="flex items-center gap-2 border-b border-line px-3 py-1.5 text-[12px]">
              <span className="truncate">{v.name}</span>{v.synthetic && <Tag tone="synth">SYNTH</Tag>}
              <span className="num ml-auto text-muted">{fmtBytes(v.bytes)}</span>
              <a className="btn btn-sm" href={`/api/camera/videos/${encodeURIComponent(v.name)}`} download><Download size={11} /></a>
              <button className="btn btn-sm btn-danger" onClick={() => void removeMedia("videos", v.name)}><Trash2 size={11} /></button>
            </div>
          ))}
        </Panel>
        <Panel title="Snapshots" icon={<Download size={14} />}>
          {!data?.snapshots.length ? <Empty>No snapshots.</Empty> : (
            <div className="grid grid-cols-3 gap-2 sm:grid-cols-4">
              {data.snapshots.slice(0, 24).map((s) => (
                <div key={s.name} className="group relative">
                  <a href={`/api/camera/snapshots/${encodeURIComponent(s.name)}`} target="_blank" rel="noreferrer">
                    <img loading="lazy" src={`/api/camera/snapshots/${encodeURIComponent(s.name)}`} alt={s.name} className="aspect-video w-full rounded border border-line object-cover" />
                  </a>
                  <div className="num truncate text-[9.5px] text-muted">{fmtTime(s.modified)} {s.synthetic && "· synth"}</div>
                  <button className="btn btn-sm btn-danger absolute right-1 top-1 hidden !p-0.5 group-hover:inline-flex" onClick={() => void removeMedia("snapshots", s.name)}><Trash2 size={10} /></button>
                </div>
              ))}
            </div>
          )}
        </Panel>
      </div>
      <EventLog />
    </div>
  );
}

export function EventLog() {
  const [tab, setTab] = useState<"events" | "logs">("events");
  const events = usePoll<TelemetryEvent[]>(tab === "events" ? "/api/events?limit=200" : null, 5000);
  const logs = usePoll<{ ts: number; level: string; logger: string; message: string }[]>(tab === "logs" ? "/api/logs?limit=300" : null, 5000);
  const rows = tab === "events"
    ? (events.data ?? []).map((e) => ({ ts: e.ts, level: e.level, src: e.source, msg: e.message }))
    : (logs.data ?? []).map((l) => ({ ts: l.ts, level: l.level.toLowerCase(), src: l.logger, msg: l.message }));
  return (
    <Panel title="Logs & Events" icon={<Database size={14} />} bodyClassName="!p-0"
      actions={<><button className={`btn btn-sm ${tab === "events" ? "btn-primary" : ""}`} onClick={() => setTab("events")}>Events</button>
        <button className={`btn btn-sm ${tab === "logs" ? "btn-primary" : ""}`} onClick={() => setTab("logs")}>Backend log</button></>}>
      <div className="num max-h-72 overflow-auto text-[11px]">
        {rows.length === 0 && <Empty>Nothing yet.</Empty>}
        {[...rows].reverse().map((r, i) => (
          <div key={i} className="flex gap-2 border-b border-line px-3 py-0.5">
            <span className="shrink-0 text-muted">{fmtTime(r.ts)}</span>
            <span className={`w-14 shrink-0 ${r.level.startsWith("err") || r.level === "critical" ? "text-crit" : r.level.startsWith("warn") ? "text-warn" : "text-dim"}`}>{r.level}</span>
            <span className="w-24 shrink-0 truncate text-cyan-dim">{r.src}</span>
            <span className="min-w-0 break-words">{r.msg}</span>
          </div>
        ))}
      </div>
    </Panel>
  );
}
