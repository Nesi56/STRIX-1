import { FileJson, FileSpreadsheet, Pause, Play, Trash2, Video } from "lucide-react";
import { droneUrl } from "../../services/drone";
import { useEffect, useMemo, useRef, useState } from "react";
import { usePoll } from "../../hooks/usePoll";
import { del, errorMessage, get } from "../../services/api";
import { fmt, fmtBytes, fmtDuration, fmtTime } from "../../lib/format";
import type { MediaFile, Session, TelemetryEvent, TelemetryRow } from "../../types/telemetry";
import { Empty, Tag } from "../common/ui";
import { useToast } from "../common/toast";
import { LiveChart } from "../telemetry/LiveChart";

interface RecordingsResponse { sessions: Session[]; videos: MediaFile[]; snapshots: MediaFile[] }
interface PlaybackData { session_id: number; total_samples: number; stride: number; rows: TelemetryRow[]; events: TelemetryEvent[] }

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
            <div className="num grid grid-cols-2 gap-1 rounded border border-line p-1.5 text-[10.5px]">
              <span>{fmtTime(at.ts)}</span>
              <span>roll {fmt(at.roll, 1, "°")}</span><span>pitch {fmt(at.pitch, 1, "°")}</span>
              <span>env {fmt(at.env_temp, 1, "°C")} / {fmt(at.humidity, 0, "%")}</span>
              <span>servo {fmt(at.servo_angle, 0, "°")}</span><span>cpu {fmt(at.cpu_percent, 0, "%")}</span>
            </div>
          )}
          <div className="grid gap-2">
            <div><div className="label">Orientation (°)</div>
              <LiveChart data={rows} xKey="ts" unit="°" height={110} xFormatter={rel} referenceX={cursor}
                series={[{ key: "roll", name: "Roll", color: "#38d6ff" }, { key: "pitch", name: "Pitch", color: "#f2b33d" }, { key: "yaw_rel", name: "Rel. yaw", color: "#c08bff" }]} />
            </div>
            <div><div className="label">Acceleration (m/s²)</div>
              <LiveChart data={rows} xKey="ts" unit="m/s²" height={110} xFormatter={rel} referenceX={cursor}
                series={[{ key: "ax", name: "X", color: "#ff5468" }, { key: "ay", name: "Y", color: "#34d58a" }, { key: "az", name: "Z", color: "#4c8dff" }]} />
            </div>
            <div><div className="label">Angular velocity (°/s)</div>
              <LiveChart data={rows} xKey="ts" unit="°/s" height={110} xFormatter={rel} referenceX={cursor}
                series={[{ key: "gx", name: "X", color: "#ff5468" }, { key: "gy", name: "Y", color: "#34d58a" }, { key: "gz", name: "Z", color: "#4c8dff" }]} />
            </div>
            <div><div className="label">Environment & system</div>
              <LiveChart data={rows} xKey="ts" unit="" height={110} xFormatter={rel} referenceX={cursor}
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

/** Recordings tab: one row per recording (sensor data + the camera video recorded with it). */
export function RecordingsTab() {
  const toast = useToast();
  const { data, refresh } = usePoll<RecordingsResponse>("/api/recordings", 4000);
  const [selected, setSelected] = useState<Session | null>(null);
  const remove = async (s: Session) => {
    if (!window.confirm(`Delete "${s.name}" (${s.samples} samples${s.metadata.video ? " + video" : ""})?`)) return;
    try {
      await del(`/api/recordings/${s.id}`);
      const v = s.metadata.video as string | undefined;
      if (v) await del(`/api/camera/videos/${encodeURIComponent(v)}`).catch(() => {});
      if (selected?.id === s.id) setSelected(null);
      await refresh();
    } catch (e) {
      toast(errorMessage(e), "error");
    }
  };
  if (selected) return <Playback session={selected} onClose={() => setSelected(null)} />;
  const sessions = data?.sessions ?? [];
  return (
    <div className="space-y-1.5 text-[11px]">
      <p className="text-dim">Press <span className="text-crit">● START RECORDING</span> (top bar) to record the camera video and all sensor data together.</p>
      {sessions.length === 0 && <Empty>No recordings yet.</Empty>}
      {sessions.map((s) => {
        const video = s.metadata.video as string | undefined;
        return (
          <div key={s.id} className="rounded-[3px] border border-line bg-[#0a0e11] p-1.5">
            <div className="flex items-center gap-1.5">
              <span className="truncate text-text">{s.name}</span>
              {s.active ? <Tag tone="crit">REC</Tag> : s.synthetic ? <Tag tone="synth">SYNTH</Tag> : null}
              <span className="ml-auto text-dim">{fmtDuration(s.duration_s)}</span>
            </div>
            <div className="mt-0.5 text-dim">{fmtTime(s.started_at, true)} · {s.samples} samples{video ? " · video" : ""}</div>
            <div className="mt-1 flex flex-wrap gap-1">
              <button className="btn btn-sm" disabled={s.active} onClick={() => setSelected(s)}><Play size={10} /> Play</button>
              {video && <a className="btn btn-sm" href={droneUrl(`/api/camera/videos/${encodeURIComponent(video)}`)} download><Video size={10} /> Video</a>}
              <a className="btn btn-sm" href={droneUrl(`/api/recordings/${s.id}/export.csv`)} download><FileSpreadsheet size={10} /> CSV</a>
              <a className="btn btn-sm" href={droneUrl(`/api/recordings/${s.id}/export.json`)} download><FileJson size={10} /> JSON</a>
              <button className="btn btn-sm btn-danger ml-auto" disabled={s.active} onClick={() => void remove(s)}><Trash2 size={10} /></button>
            </div>
          </div>
        );
      })}
      {(data?.snapshots.length ?? 0) > 0 && (
        <div className="pt-1">
          <div className="mb-1 text-[10px] tracking-[0.14em] text-muted">SNAPSHOTS</div>
          <div className="grid grid-cols-3 gap-1">
            {data!.snapshots.slice(0, 9).map((m) => (
              <a key={m.name} href={droneUrl(`/api/camera/snapshots/${encodeURIComponent(m.name)}`)} target="_blank" rel="noreferrer" title={m.name}>
                <img loading="lazy" src={droneUrl(`/api/camera/snapshots/${encodeURIComponent(m.name)}`)} alt={m.name} className="aspect-video w-full rounded-[2px] border border-line object-cover" />
              </a>
            ))}
          </div>
        </div>
      )}
      <div className="text-dim">Storage used: {fmtBytes(data?.videos.reduce((a, v) => a + v.bytes, 0) ?? 0)} video</div>
    </div>
  );
}
