import { Power } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useTelemetry } from "../../hooks/useTelemetry";
import { errorMessage, post } from "../../services/api";
import { fmt, signed } from "../../lib/format";
import { useToast } from "../common/toast";
import { ConsolePanel } from "./ConsolePanel";
import { emergencyStop } from "./TopBar";

const KEYS: { keys: string[]; label: string; id: string }[] = [
  { id: "up", keys: ["W", "↑"], label: "Tilt up" },
  { id: "down", keys: ["S", "↓"], label: "Tilt down" },
  { id: "center", keys: ["C"], label: "Center camera" },
  { id: "arm", keys: ["E"], label: "Enable / disable servo" },
  { id: "estop", keys: ["SPACE"], label: "Emergency stop" },
  { id: "stop", keys: ["ESC"], label: "Stop agent" },
];

function readStep(): number {
  try {
    return Number(localStorage.getItem("strix.step")) || 5;
  } catch {
    return 5;
  }
}

/** Keyboard + slider control of the camera tilt servo. Nothing here can move the aircraft. */
export function ManualControls() {
  const st = useTelemetry();
  const sv = st?.servo;
  const toast = useToast();
  const [step, setStep] = useState(readStep);
  const [speed, setSpeed] = useState<number | null>(null);
  const [down, setDown] = useState<string | null>(null);
  const last = useRef(0);
  const armedLocal = useRef<{ v: boolean; t: number } | null>(null);
  const svRef = useRef(sv);
  svRef.current = sv;

  useEffect(() => {
    try { localStorage.setItem("strix.step", String(step)); } catch { /* ignore */ }
  }, [step]);

  const act = useCallback(async (id: string) => {
    const s = svRef.current;
    try {
      if (id === "estop") return void (await emergencyStop(toast));
      if (id === "stop") {
        const r = await post<{ cancelled: boolean }>("/api/agent/stop");
        if (r.cancelled) toast("Agent stopped", "warn");
        return;
      }
      if (id === "arm") {
        // Use our own last toggle for ~1.5 s: telemetry may not have caught up yet.
        const recent = armedLocal.current && Date.now() - armedLocal.current.t < 1500 ? armedLocal.current.v : !!s?.armed;
        await post(recent ? "/api/servo/disarm" : "/api/servo/arm");
        armedLocal.current = { v: !recent, t: Date.now() };
        toast(recent ? "Servo control disabled" : "Servo control enabled — W/S tilt, C center", recent ? "warn" : "ok");
        return;
      }
      // No client-side gating on the armed flag: telemetry can lag ~1 s behind an E press.
      // The backend rejects motion while disabled and we show its message.
      if (id === "center") await post("/api/servo/center");
      if (id === "up") await post("/api/servo/nudge", { up_deg: step });
      if (id === "down") await post("/api/servo/nudge", { up_deg: -step });
    } catch (e) {
      toast(errorMessage(e), "error");
    }
  }, [step, toast]);

  useEffect(() => {
    const map: Record<string, string> = { w: "up", arrowup: "up", s: "down", arrowdown: "down", c: "center", e: "arm", " ": "estop", escape: "stop" };
    const onDown = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable)) return;
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const id = map[e.key.toLowerCase()];
      if (!id) return;
      e.preventDefault();
      setDown(id);
      const now = Date.now();
      if (e.repeat && (id === "arm" || id === "estop" || id === "stop" || now - last.current < 120)) return;
      last.current = now;
      void act(id);
    };
    const onUp = () => setDown(null);
    window.addEventListener("keydown", onDown);
    window.addEventListener("keyup", onUp);
    return () => {
      window.removeEventListener("keydown", onDown);
      window.removeEventListener("keyup", onUp);
    };
  }, [act]);

  const L = sv?.limits;
  const curSpeed = speed ?? L?.max_speed_dps ?? 60;
  const tiltMin = L ? (L.min_angle - L.center_angle) * (sv?.up_is_positive === false ? -1 : 1) : -60;
  const tiltMax = L ? (L.max_angle - L.center_angle) * (sv?.up_is_positive === false ? -1 : 1) : 60;
  const lo = Math.min(tiltMin, tiltMax);
  const hi = Math.max(tiltMin, tiltMax);
  const tilt = sv?.tilt_deg ?? 0;
  const pct = hi > lo ? ((tilt - lo) / (hi - lo)) * 100 : 50;

  return (
    <ConsolePanel title="Manual Controls" highlight status={sv ? { ok: !!sv.available, label: sv.available ? (sv.armed ? "Armed" : "Safe") : "Off" } : null}>
      <div className="mb-1 flex items-center justify-between">
        <div className="text-[10.5px] font-semibold uppercase tracking-[0.14em] text-text">Keyboard Controls</div>
        <span className="flex items-center gap-1 text-[10px] text-accent"><span className="h-1.5 w-1.5 rounded-full bg-accent" />OK</span>
      </div>
      <div className="grid grid-cols-2 gap-1">
        {KEYS.map((k) => (
          <button key={k.id} onClick={() => void act(k.id)}
            className={`flex items-center gap-1.5 rounded-[3px] border px-1.5 py-1 text-left text-[10.5px] transition ${down === k.id ? "border-accent bg-[#173a27]" : "border-line bg-[#0d100e] hover:border-line-2"} ${k.id === "estop" ? "text-crit" : ""}`}>
            <span className="flex gap-0.5">{k.keys.map((x) => <span key={x} className={`key ${down === k.id ? "down" : ""}`}>{x}</span>)}</span>
            <span className="truncate">{k.label}</span>
          </button>
        ))}
      </div>

      <div className="mt-3 flex items-center justify-between text-[10.5px] uppercase tracking-[0.12em] text-muted">
        <span>Tilt speed <span className="normal-case">(°/s)</span></span><span className="text-text">{curSpeed.toFixed(0)}</span>
      </div>
      <input type="range" className="slider" min={10} max={180} step={5} value={curSpeed} aria-label="Tilt speed"
        onChange={(e) => setSpeed(Number(e.target.value))}
        onPointerUp={() => speed != null && void post("/api/servo/speed", { max_speed_dps: speed }).then(() => setSpeed(null)).catch((e) => toast(errorMessage(e), "error"))}
        onKeyUp={() => speed != null && void post("/api/servo/speed", { max_speed_dps: speed }).then(() => setSpeed(null))} />

      <div className="mt-2 flex items-center justify-between text-[10.5px] uppercase tracking-[0.12em] text-muted">
        <span>Step size <span className="normal-case">(° per key)</span></span><span className="text-text">{step}</span>
      </div>
      <input type="range" className="slider-blue" min={1} max={20} step={1} value={step} aria-label="Step size" onChange={(e) => setStep(Number(e.target.value))} />

      <div className="mt-3 rounded-[3px] border border-line bg-[#0d100e] p-2">
        <div className="flex items-center justify-between">
          <span className="text-[10.5px] uppercase tracking-[0.12em] text-muted">Camera tilt</span>
          <button className={`btn btn-sm ${sv?.armed ? "btn-danger" : "btn-primary"}`} disabled={!sv?.available} onClick={() => void act("arm")}>
            <Power size={11} /> {sv?.armed ? "Disable" : "Enable"}
          </button>
        </div>
        <div className="mt-1 flex items-baseline gap-2">
          <span className="text-2xl text-text">{signed(sv?.tilt_deg, 0, "°")}</span>
          <span className="text-[11px] text-muted">servo {fmt(sv?.commanded_angle, 0, "°")} → {fmt(sv?.target_angle, 0, "°")}{sv?.moving ? " ▸" : ""}</span>
        </div>
        <div className="relative mt-1.5 h-1.5 rounded bg-white/5">
          <div className="absolute top-1/2 h-3 w-0.5 -translate-y-1/2 bg-dim" style={{ left: `${hi > lo ? ((0 - lo) / (hi - lo)) * 100 : 50}%` }} />
          <div className="absolute top-1/2 h-3 w-1.5 -translate-x-1/2 -translate-y-1/2 rounded-sm bg-accent" style={{ left: `${Math.max(0, Math.min(100, pct))}%` }} />
        </div>
        <div className="mt-0.5 flex justify-between text-[9.5px] text-dim"><span>{lo.toFixed(0)}° down</span><span>level</span><span>{hi.toFixed(0)}° up</span></div>
        {!sv?.available && <div className="mt-1 text-[10.5px] text-crit">{sv?.status.error ?? "servo unavailable"}</div>}
      </div>
      <p className="mt-1.5 text-[10px] leading-snug text-dim">Moves only the camera tilt servo (GPIO{sv?.gpio ?? 18}) — never the drone.</p>
    </ConsolePanel>
  );
}
