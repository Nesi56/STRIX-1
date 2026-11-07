import { Crosshair, Lock, Move, Unlock } from "lucide-react";
import { useRef, useState } from "react";
import { useTelemetry } from "../../hooks/useTelemetry";
import { errorMessage, post } from "../../services/api";
import { fmt } from "../../lib/format";
import { Notice, Panel, StateBadge, Tag } from "../common/ui";
import { useToast } from "../common/toast";

// Camera positioning only. This panel has no access to propulsion or flight control.
export function ServoPanel({ className }: { className?: string }) {
  const st = useTelemetry();
  const sv = st?.servo;
  const toast = useToast();
  const [draft, setDraft] = useState<number | null>(null);
  const lastSent = useRef(0);
  const pending = useRef<number | null>(null);

  const send = async (angle: number) => {
    try {
      const r = await post<{ clamped?: boolean; target_angle: number }>("/api/servo/angle", { angle });
      if (r.clamped) toast(`Clamped to safe limit ${r.target_angle.toFixed(0)}°`, "warn");
    } catch (e) {
      toast(errorMessage(e), "error");
    }
  };
  // Throttle slider commands to ≤10/s; the backend additionally slew-limits motion.
  const onSlide = (v: number) => {
    setDraft(v);
    pending.current = v;
    const now = Date.now();
    if (now - lastSent.current > 100) {
      lastSent.current = now;
      void send(v);
      pending.current = null;
    } else {
      window.setTimeout(() => {
        if (pending.current !== null && Date.now() - lastSent.current >= 100) {
          lastSent.current = Date.now();
          void send(pending.current);
          pending.current = null;
        }
      }, 120);
    }
  };
  const arm = async (on: boolean) => {
    try {
      await post(on ? "/api/servo/arm" : "/api/servo/disarm");
      setDraft(null);
    } catch (e) {
      toast(errorMessage(e), "error");
    }
  };

  if (!sv) return <Panel title="Camera Servo" icon={<Move size={14} />} className={className}><div className="text-muted">No servo data</div></Panel>;
  const L = sv.limits;
  const value = draft ?? sv.target_angle ?? sv.commanded_angle ?? L.center_angle;
  const disabled = !sv.available || !sv.armed;
  return (
    <Panel
      title="Camera Servo · SG90"
      icon={<Move size={14} />}
      className={className}
      badge={<StateBadge state={sv.status.state} />}
      actions={sv.armed ? <Tag tone="ok">CONTROL ENABLED</Tag> : <Tag tone="muted">CONTROL DISABLED</Tag>}
    >
      {!sv.available && <div className="mb-2"><Notice tone="crit">Servo unavailable: {sv.status.error}</Notice></div>}
      <div className="flex items-center gap-2">
        <button className={`btn btn-sm ${sv.armed ? "btn-danger" : "btn-primary"}`} disabled={!sv.available} onClick={() => void arm(!sv.armed)}>
          {sv.armed ? <><Lock size={12} /> Disable control</> : <><Unlock size={12} /> Enable control</>}
        </button>
        <button className="btn btn-sm" disabled={disabled} onClick={() => { setDraft(null); void post("/api/servo/center").catch((e) => toast(errorMessage(e), "error")); }}>
          <Crosshair size={12} /> Center
        </button>
        <button className="btn btn-sm" disabled={disabled} onClick={() => onSlide(Math.max(L.min_angle, value - 5))}>−5°</button>
        <button className="btn btn-sm" disabled={disabled} onClick={() => onSlide(Math.min(L.max_angle, value + 5))}>+5°</button>
      </div>
      <div className="mt-3">
        <input type="range" className="slider" min={L.min_angle} max={L.max_angle} step={1} value={value} disabled={disabled}
          aria-label="Camera tilt angle" onChange={(e) => onSlide(Number(e.target.value))} onMouseUp={() => setDraft(null)} onTouchEnd={() => setDraft(null)} />
        <div className="num flex justify-between text-[10px] text-muted">
          <span>{L.min_angle.toFixed(0)}° safe min</span><span>center {L.center_angle.toFixed(0)}°</span><span>safe max {L.max_angle.toFixed(0)}°</span>
        </div>
      </div>
      <div className="num mt-2 grid grid-cols-3 gap-2 text-[12px]">
        <div><div className="label">Target</div>{fmt(sv.target_angle, 1, "°")}</div>
        <div><div className="label">Commanded</div>{fmt(sv.commanded_angle, 1, "°")} {sv.moving && <span className="text-cyan">▸</span>}</div>
        <div><div className="label">Rate limit</div>{fmt(L.max_speed_dps, 0, "°/s")}</div>
      </div>
      <p className="mt-2 text-[10.5px] leading-snug text-muted">
        Camera positioning only · GPIO{sv.gpio} · pulses {sv.pulse_range_us[0]}–{sv.pulse_range_us[1]} µs · no position feedback (SG90): angles are commanded, not measured.
        Servo power must come from a regulated 5 V supply with common ground.
      </p>
    </Panel>
  );
}
