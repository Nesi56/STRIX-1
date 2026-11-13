import { Circle, OctagonX, RotateCcw, Square } from "lucide-react";
import { useState } from "react";
import { useTelemetry } from "../../hooks/useTelemetry";
import { errorMessage, post } from "../../services/api";
import { fmtDuration } from "../../lib/format";
import { useToast } from "../common/toast";

export async function emergencyStop(toast: (t: string, tone?: "info" | "ok" | "warn" | "error") => void) {
  try {
    await post("/api/estop");
    toast("EMERGENCY STOP — servo control disabled, agent cancelled", "warn");
  } catch (e) {
    toast(errorMessage(e), "error");
  }
}

export function TopBar({ nav }: { nav?: React.ReactNode } = {}) {
  const st = useTelemetry();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const rec = st?.recording;
  const recording = !!rec?.active;

  const toggleRec = async () => {
    setBusy(true);
    try {
      if (recording) {
        await post("/api/record/stop");
        toast("Recording saved — see the Recordings tab", "ok");
      } else {
        const r = await post<{ video: string | null; video_error: string | null }>("/api/record/start", {});
        toast(r.video ? "Recording camera video + all sensors" : `Recording sensors only (${r.video_error})`, r.video ? "ok" : "warn");
      }
    } catch (e) {
      toast(errorMessage(e), "error");
    } finally {
      setBusy(false);
    }
  };
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
      <div className="flex flex-wrap items-center gap-2 px-3 py-2 sm:gap-3">
        <div className="flex shrink-0 items-center gap-2">
          <svg viewBox="0 0 24 24" className="h-6 w-6" aria-hidden>
            <rect x="2" y="2" width="8" height="8" rx="1" fill="none" stroke="#3ddc84" strokeWidth="2" />
            <rect x="14" y="2" width="8" height="8" rx="1" fill="#3ddc84" />
            <rect x="2" y="14" width="8" height="8" rx="1" fill="#3ddc84" />
            <rect x="14" y="14" width="8" height="8" rx="1" fill="none" stroke="#3ddc84" strokeWidth="2" />
          </svg>
          <span className="text-[17px] font-bold tracking-[0.12em] text-text">STRIX-1</span>
          {!nav && <span className="hidden text-[10px] tracking-[0.14em] text-dim md:inline">AI CAMERA DRONE · QWEN + YOLO</span>}
        </div>
        {nav}
        {st?.demo_mode && <span className="hidden shrink-0 rounded border border-synth/40 bg-synth/10 px-1.5 text-[10px] tracking-wider text-synth sm:inline">SYNTHETIC DEMO DATA</span>}
        <div className="flex-1" />
        <button className={`btn shrink-0 ${recording ? "btn-danger" : ""}`} disabled={busy} onClick={() => void toggleRec()}
          title="Record the drone camera video and all sensor data together">
          {recording ? <><Square size={11} /> Stop <span className="text-crit"><Circle size={8} className="blink inline fill-current" /> {fmtDuration(rec?.session?.duration_s)}</span></>
            : <><Circle size={10} className="fill-crit text-crit" /> <span className="hidden sm:inline">Start recording</span><span className="sm:hidden">REC</span></>}
        </button>
        <button className="btn btn-estop shrink-0" onClick={() => void emergencyStop(toast)} title="Disable servo control and cancel the agent (Space)">
          <OctagonX size={14} /> <span className="hidden sm:inline">Emergency Stop</span>
        </button>
        <button className="btn btn-reset shrink-0" onClick={() => void reset()} title="Clear chat and agent state">
          <RotateCcw size={13} /> <span className="hidden md:inline">Reset Session</span>
        </button>
      </div>
    </header>
  );
}

export function ConnectionBanner({ conn, attempt }: { conn: string; attempt: number }) {
  if (conn !== "closed" && conn !== "stale") return null;
  return (
    <div role="alert" className="border-b border-crit/40 bg-crit/10 px-3 py-1 text-[11.5px] text-crit">
      <span className="blink">●</span> Connection to STRIX-1 lost — reconnecting automatically{attempt ? ` (attempt ${attempt})` : ""}. Values are frozen.
    </div>
  );
}
