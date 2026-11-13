import { ArrowRight, Brain, LogOut, OctagonX, VideoOff } from "lucide-react";
import { useEffect, useState } from "react";
import { Wordmark, useUtcClock } from "../components/common/Brand";
import { useToast } from "../components/common/toast";
import { errorMessage, post } from "../services/api";
import type { FleetDrone, FleetResponse } from "../types/fleet";

const DEFAULT_COLORS = ["#6fd3ff", "#f2b84b", "#c69bff", "#7be0a8", "#ff8a95", "#9db4ff", "#f2e05c", "#59e0c5"];
export const droneColor = (d: FleetDrone, i: number) => d.color ?? DEFAULT_COLORS[i % DEFAULT_COLORS.length];

const n = (v: number | null | undefined, digits = 0) => (v === null || v === undefined ? "—" : v.toFixed(digits));
const signed = (v: number | null | undefined, digits = 0) => (v === null || v === undefined ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(digits)}`);

/** Relative heading dial: yaw since start-up (not compass north — there is no magnetometer or GPS). */
function HeadingDial({ yaw, color, size = 44 }: { yaw: number | null; color: string; size?: number }) {
  return (
    <svg viewBox="-22 -22 44 44" width={size} height={size} className="shrink-0" aria-label={yaw === null ? "heading unknown" : `relative heading ${n(yaw)}°`}>
      <circle r="19" fill="#0b1014" stroke="#2a3540" />
      {Array.from({ length: 12 }, (_, i) => (
        <line key={i} x1="0" y1="-19" x2="0" y2={i % 3 === 0 ? -15 : -17} stroke="#55626e" strokeWidth="1" transform={`rotate(${i * 30})`} />
      ))}
      {yaw !== null && (
        <g transform={`rotate(${yaw})`}>
          <path d="M0 -14 L4 4 L0 1 L-4 4 Z" fill={color} />
        </g>
      )}
    </svg>
  );
}

/** Artificial horizon from the drone's real roll and pitch. */
function Horizon({ roll, pitch, color }: { roll: number | null; pitch: number | null; color: string }) {
  const known = roll !== null && pitch !== null;
  const shift = known ? Math.max(-14, Math.min(14, (pitch as number) * 0.5)) : 0;
  return (
    <svg viewBox="-20 -20 40 40" width={44} height={44} className="shrink-0" aria-label={known ? `roll ${n(roll)}°, pitch ${n(pitch)}°` : "attitude unknown"}>
      <defs><clipPath id="hz"><circle r="18" /></clipPath></defs>
      <g clipPath="url(#hz)">
        <rect x="-30" y="-30" width="60" height="60" fill="#0e1a24" />
        {known && (
          <g transform={`rotate(${-(roll as number)}) translate(0 ${shift})`}>
            <rect x="-30" y="0" width="60" height="30" fill="#1d1a14" />
            <line x1="-30" x2="30" y1="0" y2="0" stroke={color} strokeWidth="1.2" />
          </g>
        )}
      </g>
      <circle r="18" fill="none" stroke="#2a3540" strokeWidth="1.5" />
      <path d="M-9 0 H-3 L0 3 L3 0 H9" fill="none" stroke="#dfe6ec" strokeWidth="1.4" />
    </svg>
  );
}

function statusOf(d: FleetDrone): { label: string; cls: string } {
  if (!d.online) return { label: "NO LINK", cls: "border-crit/50 text-crit" };
  if (d.servo.estop_latched) return { label: "E-STOP", cls: "border-crit/50 bg-crit/15 text-crit" };
  if (d.agent.status && d.agent.status !== "idle") return { label: "TASKED", cls: "border-blue/50 text-blue" };
  return { label: "READY", cls: "border-ok/50 text-ok" };
}

/** Left rail: one compact row per drone. */
function AssetRow({ d, color }: { d: FleetDrone; color: string }) {
  const s = statusOf(d);
  return (
    <a href={`#/d/${d.id}`} className="group flex items-center gap-2.5 border-b border-line px-3 py-2.5 hover:bg-panel-2" title={`Open ${d.name}`}>
      <span className="h-8 w-[3px] shrink-0" style={{ background: d.online ? color : "#2a3540" }} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate text-[15px] font-semibold tracking-[0.12em]">{d.name.toUpperCase()}</span>
          <span className={`border px-1 text-[10px] font-semibold tracking-[0.14em] ${s.cls}`}>{s.label}</span>
        </div>
        <div className="num mt-0.5 flex gap-3 text-[11px] text-muted">
          <span>HDG {d.attitude.yaw === null ? "—" : `${n(((d.attitude.yaw % 360) + 360) % 360)}°r`}</span>
          <span>{d.environment.temperature_c === null ? "—" : `${n(d.environment.temperature_c, 1)}°C`}</span>
          <span>{d.objects.reduce((a, o) => a + o.count, 0)} obj</span>
        </div>
      </div>
      <HeadingDial yaw={d.online ? d.attitude.yaw : null} color={color} size={34} />
    </a>
  );
}

function Readout({ label, value, tone = "text-text", title }: { label: string; value: React.ReactNode; tone?: string; title?: string }) {
  return (
    <div title={title} className="min-w-0 border-l border-line pl-2">
      <div className="text-[10px] font-semibold tracking-[0.16em] text-dim">{label}</div>
      <div className={`num truncate text-[13px] ${tone}`}>{value}</div>
    </div>
  );
}

/** Main tile: camera with a HUD, then readouts. */
function DroneTile({ d, color, live }: { d: FleetDrone; color: string; live: boolean }) {
  const toast = useToast();
  const [frame, setFrame] = useState(0);
  useEffect(() => {
    if (live || !d.online) return;
    const t = window.setInterval(() => setFrame((f) => f + 1), 2000);
    return () => clearInterval(t);
  }, [live, d.online]);
  const src = live ? `/d/${d.id}/api/camera/stream.mjpg?fps=6` : `/d/${d.id}/api/camera/frame.jpg?n=${frame}`;
  const s = statusOf(d);
  const cpuHot = (d.system.cpu_temp_c ?? 0) >= 75;
  const stop = async () => {
    try {
      await post(`/d/${d.id}/api/estop`);
      toast(`${d.name}: camera servo and AI task stopped`, "warn");
    } catch (e) {
      toast(errorMessage(e), "error");
    }
  };
  const servo = d.servo.estop_latched ? { t: "E-STOP", tone: "text-crit" }
    : d.servo.available === false ? { t: "FAULT", tone: "text-crit" }
      : d.servo.armed ? { t: `ON ${signed(d.servo.tilt_deg)}°`, tone: "text-accent" } : { t: "SAFE", tone: "text-muted" };
  const qwen = d.qwen === "ready" ? { t: "READY", tone: "text-ok" } : d.qwen === "loading" ? { t: "STARTING", tone: "text-warn" } : { t: "OFFLINE", tone: "text-dim" };

  return (
    <article className="panel corner relative flex min-w-0 flex-col">
      <header className="flex items-center gap-2 border-b border-line px-3 py-2">
        <span className="h-3 w-3 shrink-0" style={{ background: d.online ? color : "#2a3540" }} />
        <h2 className="truncate text-[16px] font-bold tracking-[0.16em]">{d.name.toUpperCase()}</h2>
        <span className="num text-[11px] text-dim">{d.id}</span>
        <div className="ml-auto flex shrink-0 items-center gap-1.5 text-[10.5px] font-semibold tracking-[0.14em]">
          {d.demo_mode && <span className="border border-synth/50 px-1.5 text-synth" title="Simulated sensors and camera">SIM DATA</span>}
          {d.recording && <span className="border border-crit/50 px-1.5 text-crit"><span className="blink">●</span> REC</span>}
          <span className={`border px-1.5 ${s.cls}`}>{s.label}</span>
        </div>
      </header>

      <a href={`#/d/${d.id}`} className="group relative block aspect-video overflow-hidden bg-black" title={`Open ${d.name}'s console`} style={{ ["--hud" as string]: d.online ? color : "#2a3540" }}>
        {d.online ? (
          <img src={src} alt={`${d.name} camera`} className="h-full w-full object-cover" />
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-2 p-4 text-center">
            <VideoOff size={24} className="text-dim" />
            <div className="tick-label !text-crit">No link</div>
            <div className="text-[13px] text-muted">{d.error ?? "Waiting for the drone…"}</div>
            <div className="max-w-sm text-[11.5px] text-dim">Check the Pi is on and on this network, and that the hub has its token.</div>
          </div>
        )}
        <div className="hud-brackets" />
        {d.online && (
          <>
            <div className="num absolute left-3 top-9 bg-black/60 px-1.5 text-[11px] text-text">
              R {signed(d.attitude.roll, 1)}° · P {signed(d.attitude.pitch, 1)}° · HDG {n(d.attitude.yaw)}°r
            </div>
            {d.objects.length > 0 && (
              <div className="absolute bottom-2.5 left-3 flex max-w-[80%] flex-wrap gap-1">
                {d.objects.slice(0, 5).map((o) => (
                  <span key={o.label} className="border bg-black/70 px-1.5 text-[11.5px] font-medium tracking-[0.06em]" style={{ borderColor: color, color }}>
                    {o.label.toUpperCase()}{o.count > 1 ? ` ×${o.count}` : ""}
                  </span>
                ))}
              </div>
            )}
            <span className="absolute bottom-2.5 right-3 flex items-center gap-1 bg-black/70 px-2 py-0.5 text-[11.5px] tracking-[0.12em] opacity-0 transition group-hover:opacity-100">
              OPEN CONSOLE <ArrowRight size={12} />
            </span>
          </>
        )}
      </a>

      <div className="flex items-center gap-3 px-3 pt-2.5">
        <Horizon roll={d.online ? d.attitude.roll : null} pitch={d.online ? d.attitude.pitch : null} color={color} />
        <div className="grid flex-1 grid-cols-3 gap-y-2 sm:grid-cols-6">
          <Readout label="AIR" value={d.environment.temperature_c === null ? "—" : `${n(d.environment.temperature_c, 1)}°C`} tone={d.environment.stale ? "text-warn" : "text-text"} title="DHT11" />
          <Readout label="HUMID" value={d.environment.humidity_pct === null ? "—" : `${n(d.environment.humidity_pct)}%`} />
          <Readout label="PI" value={d.system.cpu_temp_c === null ? `${n(d.system.cpu_percent)}%` : `${n(d.system.cpu_temp_c)}°C`} tone={cpuHot ? "text-warn" : "text-text"}
            title={d.system.cpu_temp_c === null ? "CPU load (no temperature sensor)" : cpuHot ? "Hot: AI slowed down" : "Pi CPU temperature"} />
          <Readout label="SERVO" value={servo.t} tone={servo.tone} />
          <Readout label="QWEN" value={qwen.t} tone={qwen.tone} />
          <Readout label="MEMORY" value={`${d.memory.remembered}${d.memory.hidden ? ` · ${d.memory.hidden}H` : ""}`} title={`${d.memory.remembered} remembered, ${d.memory.hidden} hidden`} />
        </div>
      </div>

      <footer className="mt-auto flex flex-wrap items-center gap-x-3 gap-y-1.5 px-3 pb-3 pt-2.5 text-[12px]">
        <span className="text-dim" title="No GPS or other positioning on this drone; positions are never estimated">POS: NO GPS</span>
        {d.battery ? <span className="num text-muted" title={`From ${d.battery.source ?? "the drone"}`}>BATT {n(d.battery.percent)}%</span>
          : <span className="text-dim" title="The power bank reports nothing to the Pi">BATT: NO DATA</span>}
        {d.agent.status && d.agent.status !== "idle" && d.agent.task && <span className="truncate text-blue">TASK “{d.agent.task}”</span>}
        <div className="ml-auto flex gap-1.5">
          <button className="btn btn-sm btn-danger" disabled={!d.online} onClick={() => void stop()} title="Stop this drone's camera servo and AI task">
            <OctagonX size={12} /> Stop
          </button>
          <a className="btn btn-sm btn-primary" href={`#/d/${d.id}`}>Console <ArrowRight size={12} /></a>
        </div>
      </footer>
    </article>
  );
}

export async function stopAll(toast: ReturnType<typeof useToast>) {
  try {
    const r = await post<{ results: { id: string; ok: boolean; error?: string }[] }>("/api/hub/estop");
    const failed = r.results.filter((x) => !x.ok);
    if (failed.length) toast(`Stopped ${r.results.length - failed.length}; not reached: ${failed.map((f) => f.id).join(", ")}`, "error");
    else toast("All camera servos and AI tasks stopped", "warn");
  } catch (e) {
    toast(errorMessage(e), "error");
  }
}

export function FleetPage({ fleet, error }: { fleet: FleetResponse | null; error: string | null }) {
  const toast = useToast();
  const clock = useUtcClock();
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable)) return;
      if (e.key === " " && !e.repeat) {
        e.preventDefault();
        void stopAll(toast);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [toast]);
  const drones = fleet?.drones ?? [];
  const online = drones.filter((d) => d.online).length;
  // Browsers allow ~6 connections per site: live video for up to 4 drones, snapshots beyond that.
  const live = drones.length <= 4;
  const hq = fleet?.qwen;
  const logout = async () => {
    await post("/api/auth/logout").catch(() => {});
    location.reload();
  };

  return (
    <div className="flex h-full flex-col">
      <header className="z-30 shrink-0 border-b border-line bg-[#0a0e11]">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2.5">
          <Wordmark sub="FLEET · ADVISORY ONLY · PILOTS FLY" />
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11.5px] font-semibold tracking-[0.14em]">
            <span className={online === drones.length && drones.length ? "text-ok" : "text-warn"}>LINK {online}/{drones.length}</span>
            <span className="text-muted">NET LOCAL · OFFLINE OK</span>
            {hq && <span className={hq.state === "ready" ? "text-ok" : "text-dim"}><Brain size={12} className="mr-1 inline" />QWEN {hq.state.toUpperCase()}</span>}
            <span className="num text-text">{clock}</span>
          </div>
          <div className="flex-1" />
          <button className="btn btn-estop h-9" onClick={() => void stopAll(toast)} disabled={!drones.length} title="Stop every drone's camera servo and AI task (Space)">
            <OctagonX size={15} /> Stop all
          </button>
          <button className="btn h-9" onClick={() => void logout()} title="Sign out"><LogOut size={14} /></button>
        </div>
      </header>
      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        <aside className="shrink-0 border-b border-line bg-[#0a0e11] lg:w-[270px] lg:border-b-0 lg:border-r">
          <div className="tick-label flex items-center justify-between px-3 py-2">Assets <span className="num text-dim">{drones.length}</span></div>
          <div className="border-t border-line">
            {drones.map((d, i) => <AssetRow key={d.id} d={d} color={droneColor(d, i)} />)}
          </div>
          <p className="hidden px-3 py-3 text-[11px] leading-relaxed text-dim lg:block">
            HDG is heading relative to start-up (no compass). No map: the drones have no GPS, and STRIX-1 never guesses positions.
          </p>
        </aside>
        <main className="grid-bg min-h-0 flex-1 overflow-y-auto p-3 sm:p-4">
          {error && !fleet && <div className="mb-3 border border-crit/40 bg-crit/10 px-3 py-2 text-crit">Hub not reachable: {error}</div>}
          {fleet && drones.length === 0 && (
            <div className="panel corner relative mx-auto mt-16 max-w-lg p-6 text-center">
              <div className="tick-label mb-2 !text-text">No assets configured</div>
              <p className="text-muted">Add a <code>[[drone]]</code> block for each Pi to <code>hub.toml</code> and restart the hub. See <code>docs/HUB.md</code>.</p>
            </div>
          )}
          <div className={`mx-auto grid max-w-[1700px] gap-3 sm:gap-4 ${drones.length === 1 ? "max-w-3xl" : "xl:grid-cols-2"} ${drones.length > 4 ? "2xl:grid-cols-3" : ""}`}>
            {drones.map((d, i) => <DroneTile key={d.id} d={d} color={droneColor(d, i)} live={live} />)}
          </div>
        </main>
      </div>
    </div>
  );
}
