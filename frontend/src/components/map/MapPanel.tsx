import { Eye, Map as MapIcon, Navigation, Play, RotateCcw, ScanSearch, Square } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useServerNow, useTelemetry } from "../../hooks/useTelemetry";
import { usePoll } from "../../hooks/usePoll";
import { errorMessage, post } from "../../services/api";
import { fmt, fmtAge, fmtTime, signed } from "../../lib/format";
import type { MappingState, Observation, VOState } from "../../types/telemetry";
import { Bar, Empty, Notice, Panel, Tag } from "../common/ui";
import { useToast } from "../common/toast";

type Mode = "A" | "B" | "C";
const CAT_COLOR: Record<string, string> = {
  person: "#ff5468", vegetation: "#34d58a", structure: "#f2b33d", path: "#c08bff", sky: "#4c8dff", obstacle: "#ff8a3d",
  animal: "#ff8a3d", object: "#38d6ff",
};

// ---------------------------------------------------------------- Mode A
function OrientationOrigin() {
  const st = useTelemetry();
  const o = st?.imu.latest?.orientation;
  const yaw = o?.yaw_relative ?? 0;
  return (
    <div className="grid gap-3 md:grid-cols-[1fr_auto]">
      <div className="grid-bg relative flex aspect-square max-h-[320px] w-full items-center justify-center rounded border border-line">
        <svg viewBox="-110 -110 220 220" className="h-full w-full">
          {[30, 60, 90].map((r) => <circle key={r} r={r} fill="none" stroke="#172640" />)}
          {Array.from({ length: 12 }, (_, i) => i * 30).map((a) => (
            <line key={a} x1={0} y1={-90} x2={0} y2={a % 90 === 0 ? -100 : -95} stroke="#22385a" transform={`rotate(${a})`} />
          ))}
          <text y={-102} textAnchor="middle" fontSize={8} fill="#7d91ab">0° REL</text>
          <g transform={`rotate(${yaw})`}>
            <path d="M0 -38 L16 22 L0 12 L-16 22 Z" fill="#38d6ff22" stroke="#38d6ff" strokeWidth={2} strokeLinejoin="round" />
            <line x1={0} y1={-38} x2={0} y2={-80} stroke="#38d6ff" strokeDasharray="3 3" />
          </g>
          <circle r={3} fill="#38d6ff" />
          <text y={104} textAnchor="middle" fontSize={7} fill="#7d91ab">fixed origin · no translation</text>
        </svg>
      </div>
      <div className="num grid min-w-[180px] content-start gap-1 text-[12px]">
        <div className="label">Mode A · sensor orientation</div>
        <div>Roll <span className="float-right">{signed(o?.roll, 1, "°")}</span></div>
        <div>Pitch <span className="float-right">{signed(o?.pitch, 1, "°")}</span></div>
        <div>Rel. yaw <span className="float-right">{signed(yaw, 1, "°")}</span></div>
        <p className="mt-2 font-sans text-[11px] leading-snug text-muted">
          The aircraft is drawn at a fixed origin. Orientation comes from the IMU; the arrow shows yaw relative to the last
          reset (drifts — the MPU6050 has no magnetometer). No position, distance or speed is inferred.
        </p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- Mode B
function ObservationView({ obs }: { obs: Observation }) {
  const [imgUrl, setImgUrl] = useState<string | null>(null);
  useEffect(() => {
    // Show the image the analysis ran on (snapshot) or the live frame at view time.
    setImgUrl(obs.frame.snapshot ? `/api/camera/snapshots/${encodeURIComponent(obs.frame.snapshot)}` : `/api/camera/frame.jpg?t=${obs.ts}`);
  }, [obs.id, obs.frame.snapshot, obs.ts]);
  return (
    <div className="relative w-full overflow-hidden rounded border border-line bg-black" style={{ aspectRatio: `${obs.frame.width}/${obs.frame.height}` }}>
      {imgUrl && <img src={imgUrl} alt="analysed frame" className="absolute inset-0 h-full w-full object-cover opacity-80" />}
      <svg viewBox="0 0 1 1" preserveAspectRatio="none" className="absolute inset-0 h-full w-full">
        {obs.detections.filter((d) => d.category !== "sky").map((d, i) => (
          <rect key={i} x={d.bbox[0]} y={d.bbox[1]} width={d.bbox[2]} height={d.bbox[3]} fill="none"
            stroke={CAT_COLOR[d.category] ?? "#38d6ff"} strokeWidth={0.004} vectorEffect="non-scaling-stroke" />
        ))}
      </svg>
      <div className="absolute bottom-1 left-1 rounded bg-black/70 px-1.5 py-0.5 text-[10px] text-muted">
        {obs.frame.snapshot ? "snapshot" : "live frame (may differ from analysed frame)"} · image-relative coordinates only
      </div>
    </div>
  );
}

function VisualObservations({ mapping }: { mapping: MappingState | null }) {
  const toast = useToast();
  const now = useServerNow();
  const log = usePoll<Observation[]>("/api/mapping/observations?limit=30", 5000);
  const [busy, setBusy] = useState(false);
  const [sel, setSel] = useState<string | null>(null);
  const list = useMemo(() => [...(log.data ?? [])].reverse(), [log.data]);
  const current = list.find((o) => o.id === sel) ?? list[0] ?? null;
  const analyze = async () => {
    setBusy(true);
    try {
      const o = await post<Observation>("/api/mapping/analyze", {});
      setSel(o.id);
      await log.refresh();
    } catch (e) {
      toast(errorMessage(e), "error");
    } finally {
      setBusy(false);
    }
  };
  const setAuto = async (s: number) => {
    try {
      await post("/api/mapping/auto", { interval_s: s });
      toast(s ? `Automatic analysis every ${s}s (on-device only)` : "Automatic analysis disabled", "ok");
    } catch (e) {
      toast(errorMessage(e), "error");
    }
  };
  return (
    <div className="grid gap-3 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
      <div>
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <button className="btn btn-sm btn-primary" onClick={analyze} disabled={busy || !mapping?.vision.available}>
            <ScanSearch size={12} /> {busy ? "Analysing…" : "Analyse current frame"}
          </button>
          <label className="flex items-center gap-1 text-[11px] text-muted">
            Auto
            <select className="select !py-0.5" value={mapping?.vision.auto_interval_s ?? 0} onChange={(e) => void setAuto(Number(e.target.value))}>
              <option value={0}>off</option><option value={5}>5 s</option><option value={10}>10 s</option><option value={30}>30 s</option><option value={60}>60 s</option>
            </select>
          </label>
          <span className="text-[11px] text-muted">methods: {mapping?.vision.methods.join(", ") || "—"}</span>
        </div>
        {current ? <ObservationView obs={current} /> : <Empty>No observations yet — run an analysis.</Empty>}
        {current && (
          <div className="mt-2 space-y-1 text-[12px]">
            <div className="flex flex-wrap items-center gap-2">
              <span className="num text-muted">{fmtTime(current.ts)} ({fmtAge(current.ts, now)})</span>
              {current.frame.synthetic && <Tag tone="synth">SYNTHETIC FRAME</Tag>}
              <span className="text-muted">{current.processing_ms} ms</span>
            </div>
            <div>{current.summary}</div>
            <div className="flex flex-wrap gap-1">
              {current.detections.map((d, i) => (
                <span key={i} className="num rounded border border-line-2 px-1.5 py-[1px] text-[10px]" style={{ color: CAT_COLOR[d.category] }}>
                  {d.label} {(d.confidence * 100).toFixed(0)}% · {d.method}
                </span>
              ))}
            </div>
            <p className="text-[10.5px] text-muted">{current.note}</p>
          </div>
        )}
      </div>
      <div className="min-w-0">
        <div className="label mb-1">Observation log</div>
        <div className="max-h-[420px] overflow-auto rounded border border-line">
          {list.length === 0 && <Empty>Empty</Empty>}
          {list.map((o) => (
            <button key={o.id} onClick={() => setSel(o.id)}
              className={`block w-full border-b border-line px-2 py-1.5 text-left text-[11px] hover:bg-white/[.03] ${current?.id === o.id ? "bg-cyan/5" : ""}`}>
              <div className="num flex justify-between text-muted"><span>{fmtTime(o.ts)}</span><span>{o.trigger} · {o.frame.source}</span></div>
              <div className="truncate">{o.summary}</div>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- Mode C
function Trajectory({ vo }: { vo: VOState }) {
  const pts = vo.estimate?.trajectory ?? [];
  const xs = pts.map((p) => p.x);
  const zs = pts.map((p) => p.z);
  const span = Math.max(4, ...xs.map(Math.abs), ...zs.map(Math.abs)) * 1.15;
  const path = pts.map((p, i) => `${i ? "L" : "M"}${p.x.toFixed(3)} ${(-p.z).toFixed(3)}`).join(" ");
  const last = pts[pts.length - 1];
  return (
    <svg viewBox={`${-span} ${-span} ${span * 2} ${span * 2}`} className="grid-bg aspect-square w-full max-h-[340px] rounded border border-line">
      <line x1={-span} x2={span} y1={0} y2={0} stroke="#172640" strokeWidth={span / 200} />
      <line y1={-span} y2={span} x1={0} x2={0} stroke="#172640" strokeWidth={span / 200} />
      <path d={path} fill="none" stroke="#f2b33d" strokeWidth={span / 120} strokeDasharray={`${span / 40} ${span / 80}`} />
      <circle r={span / 60} fill="#7d91ab" />
      {last && <circle cx={last.x} cy={-last.z} r={span / 45} fill="#f2b33d" />}
      <text x={-span * 0.95} y={-span * 0.88} fontSize={span / 14} fill="#7d91ab">top-down · arbitrary units · EXPERIMENTAL</text>
    </svg>
  );
}

function ExperimentalMapping({ mapping }: { mapping: MappingState | null }) {
  const toast = useToast();
  const full = usePoll<MappingState>("/api/mapping", 1000);
  const m = full.data ?? mapping;
  const vo = m?.visual_odometry;
  const est = vo?.estimate;
  const q = est?.quality ?? {};
  const toggle = async (on: boolean) => {
    try {
      const r = await post<VOState>("/api/mapping/vo", { enabled: on });
      if (r.error) toast(r.error, "error");
      await full.refresh();
    } catch (e) {
      toast(errorMessage(e), "error");
    }
  };
  const tone = est?.state === "tracking" ? "ok" : est?.state?.startsWith("lost") ? "crit" : "warn";
  return (
    <div className="space-y-3">
      <Notice tone="crit">
        <Navigation size={12} className="mr-1 inline" />
        <b>{m?.position.message ?? "Position unavailable — no validated localization source."}</b>
      </Notice>
      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div>
          <div className="mb-2 flex flex-wrap items-center gap-2">
            {vo?.enabled ? (
              <button className="btn btn-sm btn-danger" onClick={() => void toggle(false)}><Square size={11} /> Stop VO</button>
            ) : (
              <button className="btn btn-sm btn-primary" onClick={() => void toggle(true)}><Play size={11} /> Start experimental VO</button>
            )}
            <button className="btn btn-sm" onClick={() => void post("/api/mapping/vo/reset").then(() => full.refresh())} disabled={!vo?.enabled}>
              <RotateCcw size={11} /> Reset
            </button>
            <Tag tone="warn">EXPERIMENTAL · NOT VALIDATED</Tag>
            {vo?.synthetic_input && <Tag tone="synth">SYNTHETIC INPUT</Tag>}
          </div>
          {vo?.enabled && est ? <Trajectory vo={vo} /> : <Empty>Visual odometry is off. It produces a relative, unscaled, drifting trace — never a position fix.</Empty>}
        </div>
        <div className="space-y-2 text-[12px]">
          <div className="label">Tracking quality</div>
          <div className="flex items-center gap-2"><Tag tone={tone}>{(est?.state ?? "off").toUpperCase()}</Tag>{vo?.error && <span className="text-crit">{vo.error}</span>}</div>
          <div className="num grid grid-cols-2 gap-x-4 gap-y-1">
            <span className="text-muted">Features</span><span>{q.features ?? "—"}</span>
            <span className="text-muted">Matches</span><span>{q.matches ?? "—"}</span>
            <span className="text-muted">Inliers</span><span>{q.inliers ?? "—"}</span>
            <span className="text-muted">Parallax</span><span>{fmt(q.parallax_px, 1, " px")}</span>
            <span className="text-muted">Accepted steps</span><span>{est?.accepted_steps ?? "—"}</span>
            <span className="text-muted">Lost events</span><span>{est?.lost_events ?? "—"}</span>
            <span className="text-muted">Path length</span><span>{fmt(est?.path_length_units, 1)} units</span>
            <span className="text-muted">Running</span><span>{fmt(est?.running_s, 0, " s")}</span>
          </div>
          <div>
            <div className="label mb-0.5">Inlier ratio</div>
            <Bar value={(q.inlier_ratio ?? 0) * 100} tone={(q.inlier_ratio ?? 0) > 0.5 ? "bg-ok" : "bg-warn"} />
          </div>
          <p className="text-[11px] leading-snug text-warn">
            Drift: {est?.drift ?? "unbounded"}. Scale: {est?.units ?? "unknown"}. Uncalibrated wide-angle lens (approx. pinhole from HFOV).
          </p>
          <div className="label mt-2">Localization sources</div>
          <table className="w-full text-[11px]">
            <tbody>
              {(m?.sources ?? []).map((s) => (
                <tr key={s.id} className="border-b border-line">
                  <td className="py-1 pr-2">{s.name}</td>
                  <td className="py-1 pr-2">
                    {s.installed ? (s.running ? <Tag tone="ok">RUNNING</Tag> : <Tag tone="muted">IDLE</Tag>) : <Tag tone="muted">NOT INSTALLED</Tag>}
                  </td>
                  <td className="py-1">{s.validated ? <Tag tone="ok">VALIDATED</Tag> : <Tag tone="warn">UNVALIDATED</Tag>}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="text-[10.5px] text-muted">{m?.imu_position}</p>
        </div>
      </div>
    </div>
  );
}

export function MapPanel({ className, defaultMode = "A" }: { className?: string; defaultMode?: Mode }) {
  const st = useTelemetry();
  const [mode, setMode] = useState<Mode>(defaultMode);
  const tabs: { id: Mode; label: string; icon: React.ReactNode }[] = [
    { id: "A", label: "A · Orientation", icon: <Navigation size={12} /> },
    { id: "B", label: "B · Visual observations", icon: <Eye size={12} /> },
    { id: "C", label: "C · Experimental mapping", icon: <MapIcon size={12} /> },
  ];
  return (
    <Panel title="Environment Observation / Map" icon={<MapIcon size={14} />} className={className}
      actions={<div className="flex gap-1">{tabs.map((t) => (
        <button key={t.id} className={`btn btn-sm ${mode === t.id ? "btn-primary" : ""}`} onClick={() => setMode(t.id)}>{t.icon}<span className="hidden sm:inline">{t.label}</span><span className="sm:hidden">{t.id}</span></button>
      ))}</div>}>
      {mode === "A" && <OrientationOrigin />}
      {mode === "B" && <VisualObservations mapping={st?.mapping ?? null} />}
      {mode === "C" && <ExperimentalMapping mapping={st?.mapping ?? null} />}
    </Panel>
  );
}
