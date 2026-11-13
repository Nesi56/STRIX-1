import { ArrowRight, Brain, Cpu, Droplets, LogOut, OctagonX, Thermometer, VideoOff } from "lucide-react";
import { useEffect, useState } from "react";
import { useToast } from "../components/common/toast";
import { errorMessage, post } from "../services/api";
import type { FleetDrone, FleetResponse } from "../types/fleet";

const DEFAULT_COLORS = ["#5fd4ff", "#ffb547", "#c08bff", "#7be08a", "#ff7a8a", "#4aa3ff", "#f2e05c", "#59e0c5"];
export const droneColor = (d: FleetDrone, i: number) => d.color ?? DEFAULT_COLORS[i % DEFAULT_COLORS.length];

const n = (v: number | null | undefined, digits = 0) => (v === null || v === undefined ? "—" : v.toFixed(digits));

export function HubMark({ size = 26 }: { size?: number }) {
  return (
    <svg viewBox="0 0 32 32" width={size} height={size} aria-hidden>
      <circle cx="16" cy="16" r="3.2" fill="#3ddc84" />
      {[0, 90, 180, 270].map((a) => (
        <g key={a} transform={`rotate(${a} 16 16)`}>
          <line x1="16" y1="12" x2="16" y2="5.5" stroke="#3ddc84" strokeWidth="1.6" />
          <circle cx="16" cy="4.5" r="2.6" fill="none" stroke="#3ddc84" strokeWidth="1.6" />
        </g>
      ))}
    </svg>
  );
}

/** A tiny artificial horizon from the drone's real roll and pitch. */
function Horizon({ roll, pitch, color }: { roll: number | null; pitch: number | null; color: string }) {
  const known = roll !== null && pitch !== null;
  const shift = known ? Math.max(-14, Math.min(14, (pitch as number) * 0.5)) : 0;
  return (
    <svg viewBox="-20 -20 40 40" className="h-11 w-11 shrink-0" aria-label={known ? `roll ${n(roll)}°, pitch ${n(pitch)}°` : "attitude unknown"}>
      <defs><clipPath id="hz"><circle r="18" /></clipPath></defs>
      <g clipPath="url(#hz)">
        <rect x="-30" y="-30" width="60" height="60" fill="#0f1a24" />
        {known && (
          <g transform={`rotate(${-(roll as number)}) translate(0 ${shift})`}>
            <rect x="-30" y="0" width="60" height="30" fill="#2a2216" />
            <line x1="-30" x2="30" y1="0" y2="0" stroke={color} strokeWidth="1.2" />
          </g>
        )}
      </g>
      <circle r="18" fill="none" stroke="#2e3731" strokeWidth="1.5" />
      <path d="M-9 0 H-3 L0 3 L3 0 H9" fill="none" stroke="#d6dfd9" strokeWidth="1.4" />
    </svg>
  );
}

function Stat({ icon, label, value, tone = "text-text", title }: { icon: React.ReactNode; label: string; value: React.ReactNode; tone?: string; title?: string }) {
  return (
    <div title={title} className="min-w-0 rounded-md border border-line bg-panel-2/60 px-2 py-1.5">
      <div className="flex items-center gap-1 text-[9.5px] tracking-[0.12em] text-muted">{icon}{label}</div>
      <div className={`num mt-0.5 truncate text-[13px] ${tone}`}>{value}</div>
    </div>
  );
}

function qwenText(q: string | null) {
  if (q === "ready") return { text: "Qwen ready", tone: "text-accent" };
  if (q === "loading") return { text: "Qwen starting", tone: "text-warn" };
  if (!q) return { text: "Qwen —", tone: "text-muted" };
  return { text: q === "unreachable" ? "Qwen offline" : `Qwen ${q}`, tone: "text-muted" };
}

function DroneCard({ d, color, live }: { d: FleetDrone; color: string; live: boolean }) {
  const toast = useToast();
  const [frame, setFrame] = useState(0);
  useEffect(() => {
    if (live || !d.online) return;
    const t = window.setInterval(() => setFrame((f) => f + 1), 2000);
    return () => clearInterval(t);
  }, [live, d.online]);
  const src = live ? `/d/${d.id}/api/camera/stream.mjpg?fps=6` : `/d/${d.id}/api/camera/frame.jpg?n=${frame}`;
  const cpuHot = (d.system.cpu_temp_c ?? 0) >= 75;
  const q = qwenText(d.qwen);
  const stop = async () => {
    try {
      await post(`/d/${d.id}/api/estop`);
      toast(`${d.name}: camera servo stopped`, "warn");
    } catch (e) {
      toast(errorMessage(e), "error");
    }
  };
  const servo = d.servo.estop_latched ? { t: "E-STOP", tone: "text-crit" }
    : d.servo.available === false ? { t: "not working", tone: "text-crit" }
      : d.servo.armed ? { t: `on · ${d.servo.tilt_deg === null ? "—" : `${d.servo.tilt_deg > 0 ? "+" : ""}${n(d.servo.tilt_deg)}°`}`, tone: "text-accent" }
        : { t: "off", tone: "text-muted" };

  return (
    <article className="relative flex min-w-0 flex-col overflow-hidden rounded-xl border border-line bg-panel shadow-[0_8px_30px_-12px_#000]"
      style={{ boxShadow: d.online ? `inset 0 2px 0 ${color}` : undefined }}>
      <header className="flex items-center gap-2 px-3 pb-2 pt-2.5">
        <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: d.online ? color : "#5a655e", boxShadow: d.online ? `0 0 8px ${color}` : undefined }} />
        <h2 className="truncate text-[15px] font-semibold tracking-[0.08em]">{d.name}</h2>
        <span className="num text-[10px] text-dim">{d.id}</span>
        <div className="ml-auto flex shrink-0 items-center gap-1.5">
          {d.demo_mode && <span className="rounded border border-synth/40 bg-synth/10 px-1.5 text-[9.5px] tracking-wider text-synth" title="This drone is running with simulated sensors">DEMO DATA</span>}
          {d.recording && <span className="rounded border border-crit/40 bg-crit/10 px-1.5 text-[9.5px] tracking-wider text-crit">● REC</span>}
          <span className={`rounded border px-1.5 text-[9.5px] tracking-wider ${d.online ? "border-accent/40 bg-accent/10 text-accent" : "border-crit/40 bg-crit/10 text-crit"}`}>
            {d.online ? "ONLINE" : "OFFLINE"}
          </span>
        </div>
      </header>

      <a href={`#/d/${d.id}`} className="group relative mx-3 block aspect-video overflow-hidden rounded-lg border border-line bg-black" title={`Open ${d.name}'s console`}>
        {d.online ? (
          <img src={src} alt={`${d.name} camera`} className="h-full w-full object-cover" />
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-2 p-4 text-center text-muted">
            <VideoOff size={22} />
            <div className="text-[12px]">{d.error ?? "Waiting for the drone…"}</div>
            <div className="text-[10.5px] text-dim">Check that the Pi is on, on the same Wi-Fi, and that its token is set for the hub.</div>
          </div>
        )}
        {d.online && d.objects.length > 0 && (
          <div className="absolute bottom-1.5 left-1.5 flex max-w-[85%] flex-wrap gap-1">
            {d.objects.slice(0, 5).map((o) => (
              <span key={o.label} className="rounded bg-black/70 px-1.5 py-[1px] text-[10.5px] text-text backdrop-blur-sm">
                {o.label}{o.count > 1 ? ` ×${o.count}` : ""}
              </span>
            ))}
          </div>
        )}
        <span className="absolute right-1.5 top-1.5 flex items-center gap-1 rounded bg-black/70 px-2 py-0.5 text-[11px] opacity-0 transition group-hover:opacity-100">
          Open console <ArrowRight size={12} />
        </span>
      </a>

      <div className="grid grid-cols-[auto_1fr] items-center gap-3 px-3 pt-2.5">
        <Horizon roll={d.attitude.roll} pitch={d.attitude.pitch} color={color} />
        <div className="num grid grid-cols-3 gap-1 text-[11px]">
          <div><div className="text-[9.5px] tracking-[0.12em] text-muted">ROLL</div>{n(d.attitude.roll, 1)}°</div>
          <div><div className="text-[9.5px] tracking-[0.12em] text-muted">PITCH</div>{n(d.attitude.pitch, 1)}°</div>
          <div><div className="text-[9.5px] tracking-[0.12em] text-muted">YAW</div>{n(d.attitude.yaw, 0)}°</div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-1.5 px-3 pt-2.5 sm:grid-cols-4">
        <Stat icon={<Thermometer size={10} />} label="AIR" value={d.environment.temperature_c === null ? "—" : `${n(d.environment.temperature_c, 1)} °C`}
          tone={d.environment.stale ? "text-warn" : "text-text"} title={d.environment.stale ? "Reading is old" : "DHT11"} />
        <Stat icon={<Droplets size={10} />} label="HUMIDITY" value={d.environment.humidity_pct === null ? "—" : `${n(d.environment.humidity_pct)} %`} />
        <Stat icon={<Cpu size={10} />} label="PI" value={d.system.cpu_temp_c === null ? `${n(d.system.cpu_percent)} % cpu` : `${n(d.system.cpu_temp_c)} °C`}
          tone={cpuHot ? "text-warn" : "text-text"} title={cpuHot ? "Hot: the drone slows its AI down" : "Pi CPU"} />
        <Stat icon={<span className="text-[10px]">⟂</span>} label="SERVO" value={servo.t} tone={servo.tone} />
      </div>

      <footer className="mt-auto flex flex-wrap items-center gap-x-3 gap-y-1.5 px-3 pb-3 pt-2.5 text-[11px]">
        <span className={`flex items-center gap-1 ${q.tone}`}><Brain size={12} /> {q.text}</span>
        <span className="text-muted">{d.memory.remembered} remembered{d.memory.hidden ? ` · ${d.memory.hidden} hidden` : ""}</span>
        {d.battery && <span className="num text-muted" title={`From ${d.battery.source ?? "the drone"}`}>battery {n(d.battery.percent)} %</span>}
        {d.agent.status && d.agent.status !== "idle" && d.agent.task && <span className="truncate text-blue">“{d.agent.task}”</span>}
        <div className="ml-auto flex gap-1.5">
          <button className="btn btn-sm btn-danger" disabled={!d.online} onClick={() => void stop()} title="Stop this drone's camera servo and AI task">
            <OctagonX size={11} /> Stop
          </button>
          <a className="btn btn-sm btn-primary" href={`#/d/${d.id}`}>Open <ArrowRight size={11} /></a>
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
      <header className="z-30 shrink-0 border-b border-line bg-[#0d100e]">
        <div className="flex flex-wrap items-center gap-3 px-4 py-2.5">
          <HubMark />
          <div>
            <div className="text-[17px] font-bold leading-tight tracking-[0.12em]">STRIX-1 <span className="text-accent">HUB</span></div>
            <div className="text-[10px] tracking-[0.14em] text-dim">AI CO-PILOTS · ADVISORY ONLY · PILOTS FLY</div>
          </div>
          <div className="ml-2 flex items-center gap-2 text-[11.5px]">
            <span className="num rounded-md border border-line bg-panel px-2 py-1">{online}/{drones.length} online</span>
            {hq && (
              <span className={`rounded-md border border-line bg-panel px-2 py-1 ${hq.state === "ready" ? "text-accent" : "text-muted"}`}
                title={hq.relay ? `Drones reach the shared Qwen on port ${hq.relay.split(":").pop()}` : undefined}>
                <Brain size={12} className="mr-1 inline" />shared Qwen: {hq.state}
              </span>
            )}
          </div>
          <div className="flex-1" />
          <button className="btn btn-estop" onClick={() => void stopAll(toast)} disabled={!drones.length} title="Stop every drone's camera servo and AI task (Space)">
            <OctagonX size={14} /> Stop all
          </button>
          <button className="btn" onClick={() => void logout()} title="Sign out"><LogOut size={13} /></button>
        </div>
      </header>
      <main className="min-h-0 flex-1 overflow-y-auto p-3 sm:p-4">
        {error && !fleet && <div className="mb-3 rounded-md border border-crit/40 bg-crit/10 px-3 py-2 text-crit">Hub not reachable: {error}</div>}
        {fleet && drones.length === 0 && (
          <div className="mx-auto mt-16 max-w-lg rounded-xl border border-line bg-panel p-6 text-center">
            <div className="mb-2 text-[15px] font-semibold">No drones yet</div>
            <p className="text-muted">Add a <code>[[drone]]</code> block for each Pi to <code>hub.toml</code> and restart the hub. See <code>docs/HUB.md</code>.</p>
          </div>
        )}
        <div className={`mx-auto grid max-w-[1800px] gap-3 sm:gap-4 ${drones.length === 1 ? "max-w-3xl" : "md:grid-cols-2"} ${drones.length > 4 ? "xl:grid-cols-3" : ""}`}>
          {drones.map((d, i) => <DroneCard key={d.id} d={d} color={droneColor(d, i)} live={live} />)}
        </div>
      </main>
    </div>
  );
}
