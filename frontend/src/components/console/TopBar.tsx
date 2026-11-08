import { OctagonX, RotateCcw } from "lucide-react";
import { useTelemetrySnapshot } from "../../hooks/useTelemetry";
import { errorMessage, post } from "../../services/api";
import { useToast } from "../common/toast";

export type View = "console" | "recordings" | "system" | "calibration";
const VIEWS: { id: View; label: string }[] = [
  { id: "console", label: "Console" },
  { id: "recordings", label: "Recordings" },
  { id: "system", label: "System" },
  { id: "calibration", label: "Calibration" },
];

function Dot({ tone, label, title }: { tone: "ok" | "warn" | "crit" | "synth" | "dim"; label: string; title?: string }) {
  const c = { ok: "bg-accent", warn: "bg-warn", crit: "bg-crit", synth: "bg-synth", dim: "bg-dim" }[tone];
  return (
    <span title={title} className="flex shrink-0 items-center gap-1.5 text-[11px] uppercase tracking-wider text-muted">
      <span className={`h-2 w-2 rounded-full ${c} ${tone === "ok" ? "shadow-[0_0_6px_#3ddc84]" : ""}`} />
      {label}
    </span>
  );
}

const stateTone = (s?: string): "ok" | "warn" | "crit" | "synth" | "dim" =>
  s === "ok" || s === "ready" ? "ok" : s === "synthetic" ? "synth" : s === "degraded" || s === "loading" || s === "starting" ? "warn"
    : s === "disabled" ? "dim" : "crit";

export async function emergencyStop(toast: (t: string, tone?: "info" | "ok" | "warn" | "error") => void) {
  try {
    await post("/api/estop");
    toast("EMERGENCY STOP — servo control disabled, agent cancelled", "warn");
  } catch (e) {
    toast(errorMessage(e), "error");
  }
}

export function TopBar({ view, setView }: { view: View; setView: (v: View) => void }) {
  const snap = useTelemetrySnapshot();
  const st = snap.state;
  const toast = useToast();
  const det = st?.detector;
  const qwen = st?.ai?.qwen;
  const link = snap.conn;
  const reset = async () => {
    if (!window.confirm("Reset session? This cancels the agent, clears the chat, zeroes relative yaw and resets min/max.")) return;
    try {
      await post("/api/session/reset");
      window.dispatchEvent(new Event("strix:reset"));
      toast("Session reset", "ok");
    } catch (e) {
      toast(errorMessage(e), "error");
    }
  };
  return (
    <header className="z-30 shrink-0 border-b border-line bg-[#0d100e]">
      <div className="flex items-center gap-4 px-3 py-2">
        <div className="flex shrink-0 items-center gap-2">
          <svg viewBox="0 0 24 24" className="h-6 w-6" aria-hidden>
            <rect x="2" y="2" width="8" height="8" rx="1" fill="none" stroke="#3ddc84" strokeWidth="2" />
            <rect x="14" y="2" width="8" height="8" rx="1" fill="#3ddc84" />
            <rect x="2" y="14" width="8" height="8" rx="1" fill="#3ddc84" />
            <rect x="14" y="14" width="8" height="8" rx="1" fill="none" stroke="#3ddc84" strokeWidth="2" />
          </svg>
          <span className="text-[17px] font-bold tracking-[0.12em] text-text">STRIX-1</span>
        </div>
        <div className="flex min-w-0 flex-1 items-center gap-4 overflow-x-auto">
          <Dot tone={stateTone(det?.status.state)} label="YOLO Stream" title={det ? `${det.description} · ${det.fps} fps` : "detector"} />
          <Dot tone={stateTone(qwen?.state)} label={qwen?.state === "ready" ? "Qwen" : `Qwen ${qwen?.state ?? "…"}`} title={qwen?.detail ?? qwen?.model_id} />
          <Dot tone={stateTone(st?.imu.status.state)} label="MPU6050" title={st?.imu.status.error ?? undefined} />
          <Dot tone={stateTone(st?.environment?.status.state)} label="DHT11" title={st?.environment?.status.error ?? undefined} />
          <Dot tone={link === "open" ? "ok" : link === "connecting" || link === "stale" ? "warn" : "crit"}
            label={link === "open" ? `Link ${snap.rttMs != null ? snap.rttMs.toFixed(0) : "…"} ms` : `Link ${link}`} />
          {st?.demo_mode && <span className="shrink-0 rounded border border-synth/40 bg-synth/10 px-1.5 text-[10px] tracking-wider text-synth">SYNTHETIC DEMO DATA</span>}
        </div>
        <nav className="hidden shrink-0 gap-1 lg:flex">
          {VIEWS.map((v) => <button key={v.id} className={`chip-tab ${view === v.id ? "active" : ""}`} onClick={() => setView(v.id)}>{v.label}</button>)}
        </nav>
        <button className="btn btn-estop shrink-0" onClick={() => void emergencyStop(toast)} title="Disable servo control and cancel the agent (Space)">
          <OctagonX size={14} /> <span className="hidden sm:inline">Emergency Stop</span>
        </button>
        <button className="btn btn-reset shrink-0" onClick={() => void reset()} title="Clear chat and agent state">
          <RotateCcw size={13} /> <span className="hidden sm:inline">Reset Session</span>
        </button>
      </div>
      <nav className="flex gap-1 overflow-x-auto px-3 pb-1.5 lg:hidden">
        {VIEWS.map((v) => <button key={v.id} className={`chip-tab ${view === v.id ? "active" : ""}`} onClick={() => setView(v.id)}>{v.label}</button>)}
      </nav>
      {(link === "closed" || link === "stale") && (
        <div role="alert" className="border-t border-crit/40 bg-crit/10 px-3 py-1 text-[11.5px] text-crit">
          <span className="blink">●</span> Connection to STRIX-1 lost — reconnecting automatically{snap.reconnectAttempt ? ` (attempt ${snap.reconnectAttempt})` : ""}. Values are frozen.
        </div>
      )}
    </header>
  );
}
