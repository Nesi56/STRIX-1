import { Activity, Bot, Camera, Cpu, MemoryStick, Radio, Thermometer, Zap } from "lucide-react";
import type { ReactNode } from "react";
import { useTelemetrySnapshot } from "../../hooks/useTelemetry";
import { fmt } from "../../lib/format";
import type { DeviceStatus } from "../../types/telemetry";

export type Page = "dashboard" | "telemetry" | "environment" | "recordings" | "system" | "settings";
export const PAGES: { id: Page; label: string }[] = [
  { id: "dashboard", label: "Dashboard" },
  { id: "telemetry", label: "Telemetry" },
  { id: "environment", label: "Environment / Map" },
  { id: "recordings", label: "Recordings & Logs" },
  { id: "system", label: "System" },
  { id: "settings", label: "Settings & Calibration" },
];

function Chip({ icon, label, value, tone = "default", title }: { icon: ReactNode; label: string; value: ReactNode; tone?: "default" | "ok" | "warn" | "crit" | "synth"; title?: string }) {
  const t = { default: "text-text", ok: "text-ok", warn: "text-warn", crit: "text-crit", synth: "text-synth" }[tone];
  return (
    <div title={title} className="flex shrink-0 items-center gap-1.5 rounded-md border border-line bg-[#060c17] px-2 py-1">
      <span className="text-muted">{icon}</span>
      <span className="label !text-[9px]">{label}</span>
      <span className={`num text-[12px] ${t}`}>{value}</span>
    </div>
  );
}

function dot(s?: DeviceStatus | null) {
  const c = !s ? "bg-dim" : s.state === "ok" ? "bg-ok" : s.state === "synthetic" ? "bg-synth" : s.state === "degraded" ? "bg-warn"
    : s.state === "disabled" ? "bg-dim" : "bg-crit";
  return <span title={`${s?.name ?? "?"}: ${s?.state ?? "unknown"}${s?.error ? ` — ${s.error}` : ""}`} className={`inline-block h-2 w-2 rounded-full ${c}`} />;
}

export function Header({ page, setPage, onToggleAI }: { page: Page; setPage: (p: Page) => void; onToggleAI: () => void }) {
  const snap = useTelemetrySnapshot();
  const st = snap.state;
  const sys = st?.system;
  const link = snap.conn;
  const linkTone = link === "open" ? "ok" : link === "connecting" || link === "stale" ? "warn" : "crit";
  const temp = sys?.cpu_temp_c;
  const power = sys?.power;
  const crit = sys?.warnings?.some((w) => w.severity === "critical");
  const qwen = st?.ai?.qwen;
  return (
    <header className="sticky top-0 z-30 border-b border-line bg-[#03060cf0] backdrop-blur">
      <div className="flex items-center gap-3 px-3 py-2">
        <div className="flex shrink-0 items-center gap-2">
          <svg viewBox="0 0 64 64" className="h-7 w-7" aria-hidden><path d="M32 8 L56 30 L45 30 L32 54 L19 30 L8 30 Z" fill="none" stroke="#38d6ff" strokeWidth="4" strokeLinejoin="round" /><circle cx="25" cy="27" r="3" fill="#38d6ff" /><circle cx="39" cy="27" r="3" fill="#38d6ff" /></svg>
          <div className="leading-none">
            <div className="text-[15px] font-semibold tracking-[0.28em]">STRIX-1</div>
            <div className="label !text-[8.5px]">Aerial sensing ground station</div>
          </div>
        </div>
        <div className="flex min-w-0 flex-1 items-center gap-1.5 overflow-x-auto pb-0.5">
          <Chip icon={<Radio size={12} />} label="Link" tone={linkTone}
            value={link === "open" ? `${snap.rttMs != null ? snap.rttMs.toFixed(0) : "…"} ms` : link.toUpperCase()} title="WebSocket telemetry link (round-trip time)" />
          <Chip icon={<Camera size={12} />} label="Cam" value={st?.camera?.available ? `${fmt(st.camera.measured_fps, 0)} fps` : "OFF"} tone={st?.camera?.available ? "default" : "crit"} />
          <Chip icon={<Cpu size={12} />} label="CPU" value={fmt(sys?.cpu_percent, 0, "%")} tone={(sys?.cpu_percent ?? 0) > 90 ? "warn" : "default"} />
          <Chip icon={<MemoryStick size={12} />} label="RAM" value={fmt(sys?.memory.used_percent, 0, "%")} tone={(sys?.memory.used_percent ?? 0) > 85 ? "warn" : "default"} />
          <Chip icon={<Thermometer size={12} />} label="SoC" value={fmt(temp, 0, "°C")} tone={temp != null && temp >= 80 ? "crit" : temp != null && temp >= 70 ? "warn" : "default"} />
          <Chip icon={<Activity size={12} />} label="Sensors" value={<span className="flex items-center gap-1">{dot(st?.imu.status)}{dot(st?.environment?.status)}{dot(st?.camera?.status)}{dot(st?.servo?.status)}</span>}
            title="IMU · DHT11 · Camera · Servo" />
          <Chip icon={<Bot size={12} />} label="AI" value={qwen ? (qwen.state === "ready" ? (qwen.last?.tokens_per_s ? `${qwen.last.tokens_per_s.toFixed(1)} t/s` : "READY") : qwen.state.toUpperCase()) : "—"}
            tone={qwen?.state === "ready" ? "ok" : qwen?.state === "loading" ? "warn" : "default"} />
          <Chip icon={<Zap size={12} />} label="Power" tone={power?.undervoltage_now ? "crit" : power?.undervoltage_occurred ? "warn" : "default"}
            value={power?.undervoltage_now ? "UNDERVOLT" : power?.input_voltage_v != null ? `${power.input_voltage_v.toFixed(2)} V` : power?.undervoltage_now === false ? "OK" : "n/a"}
            title={power?.battery?.reason ?? "battery telemetry unavailable"} />
          {crit && <Chip icon={<Zap size={12} />} label="Alert" tone="crit" value="CHECK SYSTEM" />}
        </div>
        <button className="btn btn-sm shrink-0" onClick={onToggleAI} title="Toggle AI assistant"><Bot size={13} /><span className="hidden md:inline">Assistant</span></button>
      </div>
      <nav className="flex gap-1 overflow-x-auto px-3 pb-1.5">
        {PAGES.map((p) => (
          <button key={p.id} onClick={() => setPage(p.id)}
            className={`shrink-0 rounded-md px-2.5 py-1 text-[12px] transition ${page === p.id ? "bg-cyan/10 text-cyan ring-1 ring-cyan/30" : "text-muted hover:text-text"}`}>
            {p.label}
          </button>
        ))}
      </nav>
    </header>
  );
}

export function StatusBanners() {
  const snap = useTelemetrySnapshot();
  const st = snap.state;
  const lost = snap.conn === "closed" || snap.conn === "stale";
  const since = snap.disconnectedSince ? Math.round((Date.now() - snap.disconnectedSince) / 1000) : null;
  return (
    <>
      {lost && (
        <div role="alert" className="flex items-center gap-2 border-b border-crit/40 bg-crit/10 px-3 py-1.5 text-[12px] text-crit">
          <span className="blink">●</span>
          {snap.conn === "stale" ? "Telemetry stalled — no data for several seconds." : "Connection to STRIX-1 lost."}
          <span className="text-muted">Reconnecting automatically{snap.reconnectAttempt ? ` (attempt ${snap.reconnectAttempt})` : ""}{since ? ` · ${since}s` : ""}. Displayed values are frozen.</span>
        </div>
      )}
      {st?.demo_mode && (
        <div className="border-b border-synth/30 bg-synth/10 px-3 py-1 text-center text-[11px] tracking-wide text-synth">
          DEMO MODE — some or all devices are producing clearly labeled SYNTHETIC data, not real measurements
        </div>
      )}
    </>
  );
}
