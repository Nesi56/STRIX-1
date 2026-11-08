import { Crosshair, Eraser } from "lucide-react";
import { useState } from "react";
import { useServerNow, useTelemetry } from "../../hooks/useTelemetry";
import { fmtAgo, MEM_COLOR, outOfSight } from "../../lib/memory";
import { errorMessage, post } from "../../services/api";
import type { MemoryObject } from "../../types/telemetry";
import { useToast } from "../common/toast";

const ORDER = { hidden: 0, remembered: 1, seen: 2 } as const;

interface AimResult { target: string; guidance: string[]; tilt_deg: number; state: string }

/** What STRIX-1 remembers: SEEN now, REMEMBERED (out of sight, with timers) and HIDDEN (inside a container). */
export function MemoryTab() {
  const st = useTelemetry();
  const toast = useToast();
  const serverNow = useServerNow(250);
  const [busy, setBusy] = useState<number | null>(null);
  const mem = st?.memory ?? null;
  const armed = !!st?.servo?.armed;
  const scene = mem?.scene ?? null;

  const aim = async (o: MemoryObject) => {
    setBusy(o.id);
    try {
      const r = await post<AimResult>("/api/memory/aim", { target: `#${o.id}` });
      toast(`Camera aimed at ${r.target} (tilt ${r.tilt_deg.toFixed(0)}°). ${r.guidance.join(" ")}`, "ok");
    } catch (e) {
      toast(errorMessage(e), "error");
    } finally {
      setBusy(null);
    }
  };
  const setScene = (s: "cups" | "outdoor") =>
    void post("/api/demo/scene", { scene: s }).then(() => toast(`Demo scene: ${s === "cups" ? "shell game" : "outdoor"}`, "ok")).catch((e) => toast(errorMessage(e), "error"));

  if (!mem) return <div className="text-[11px] text-dim">Waiting for the object memory…</div>;
  const objs = [...mem.objects].sort((a, b) => ORDER[a.memory] - ORDER[b.memory] || a.id - b.id);

  return (
    <div className="space-y-2 text-[11px]">
      <div className="flex flex-wrap items-center gap-2">
        <span style={{ color: MEM_COLOR.seen }}>SEEN {mem.counts.seen}</span>
        <span style={{ color: MEM_COLOR.remembered }}>REMEMBERED {mem.counts.remembered}</span>
        <span style={{ color: MEM_COLOR.hidden }}>HIDDEN {mem.counts.hidden}</span>
        <button className="btn btn-sm ml-auto" title="Forget everything" onClick={() => void post("/api/memory/clear").catch((e) => toast(errorMessage(e), "error"))}>
          <Eraser size={11} /> Clear
        </button>
      </div>
      {scene && (
        <div className="flex flex-wrap items-center gap-1 rounded-[3px] border border-synth/30 bg-synth/5 px-2 py-1">
          <span className="text-synth">DEMO SCENE</span>
          <button className={`chip-tab ${scene.scene === "cups" ? "active" : ""}`} onClick={() => setScene("cups")}>Shell game</button>
          <button className={`chip-tab ${scene.scene === "outdoor" ? "active" : ""}`} onClick={() => setScene("outdoor")}>Outdoor</button>
          {scene.phase && <span className="w-full truncate text-[10px] text-muted">{scene.t?.toFixed(0)}/{scene.cycle_s?.toFixed(0)} s · {scene.phase}</span>}
        </div>
      )}
      {objs.length === 0 && <div className="text-dim">Nothing remembered yet. Objects of the YOLO prompt classes (and cups/bowls) are remembered once seen twice.</div>}
      <ul className="space-y-1">
        {objs.map((o) => {
          const color = MEM_COLOR[o.memory];
          const t = outOfSight(o, mem, serverNow);
          return (
            <li key={o.id} className="rounded-[3px] border border-line bg-black/20 px-2 py-1" style={{ borderLeft: `3px solid ${color}` }}>
              <div className="flex items-center gap-2">
                <span className="font-semibold text-text">{o.name}</span>
                <span className="rounded-[2px] px-1 text-[9.5px] font-semibold" style={{ color, background: `${color}22` }}>{o.memory.toUpperCase()}</span>
                {o.memory !== "seen" && <span className="tabular-nums" style={{ color }}>{fmtAgo(t)}</span>}
                <button className="btn btn-sm ml-auto" disabled={!armed || busy === o.id}
                  title={armed ? "Aim the camera at it and get yaw advice" : "Enable servo control (E) first"} onClick={() => void aim(o)}>
                  <Crosshair size={10} /> AIM
                </button>
              </div>
              <div className="mt-0.5 flex flex-wrap gap-x-2 text-[10px] text-muted">
                {o.memory === "hidden" && <span style={{ color: MEM_COLOR.hidden }}>inside {o.contained_by_name}</span>}
                {o.memory === "remembered" && <span>{o.state === "out_of_view" ? "camera looked away" : "not detected where expected"}</span>}
                {o.contains.length > 0 && <span style={{ color: MEM_COLOR.hidden }}>contains {o.contains.map((id) => mem.objects.find((x) => x.id === id)?.name ?? `#${id}`).join(", ")}</span>}
                <span>az {o.az >= 0 ? "+" : ""}{o.az.toFixed(0)}° · el {o.el >= 0 ? "+" : ""}{o.el.toFixed(0)}°</span>
                <span>{o.range_m == null ? "range ?" : `~${o.range_m.toFixed(2)} m`}</span>
                {o.relocated_by_deg != null && <span className="text-warn">memory corrected {o.relocated_by_deg.toFixed(0)}°</span>}
              </div>
            </li>
          );
        })}
      </ul>
      {mem.events.length > 0 && (
        <div>
          <div className="mb-0.5 text-[10px] tracking-[0.12em] text-muted">MEMORY EVENTS</div>
          <ul className="space-y-0.5 text-[10.5px]">
            {[...mem.events].reverse().slice(0, 7).map((e, i) => (
              <li key={`${e.ts}-${i}`} className="flex gap-2">
                <span className="w-12 shrink-0 text-right tabular-nums text-dim">{fmtAgo(Math.max(0, serverNow - e.ts))}</span>
                <span className={e.kind === "hidden" || e.kind === "revealed" ? "text-warn" : e.kind === "relocated" || e.kind === "not_there" ? "text-crit" : "text-muted"}>{e.text}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="text-[9.5px] leading-snug text-dim">
        Directions are relative to the drone (yaw from the IMU drifts slowly, no compass); the drone is assumed to hold its position.
        AIM moves only the camera tilt servo and tells the pilot how to yaw. Ask Qwen: “where is the ball?”
      </div>
    </div>
  );
}
