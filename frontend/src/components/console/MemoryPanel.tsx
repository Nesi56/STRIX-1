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
export function MemoryPanel() {
  const st = useTelemetry();
  const toast = useToast();
  const mem = st?.memory ?? null;
  return (
    <section className="panel flex h-full min-h-0 flex-col">
      <header className="panel-head flex items-center gap-2 px-2.5 py-1.5">
        <h2 className="text-[10.5px] font-semibold uppercase tracking-[0.14em] text-text">Memory</h2>
        {mem && (
          <span className="truncate text-[10px]">
            {(["seen", "remembered", "hidden"] as const).filter((k) => mem.counts[k] > 0).map((k, i) => (
              <span key={k} style={{ color: MEM_COLOR[k] }}>{i > 0 && <span className="text-dim"> · </span>}{mem.counts[k]} {k}</span>
            ))}
          </span>
        )}
        <button className="ml-auto text-muted hover:text-crit" title="Forget everything" onClick={() => void post("/api/memory/clear").catch((e) => toast(errorMessage(e), "error"))}>
          <Eraser size={12} />
        </button>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto p-2"><MemoryList /></div>
    </section>
  );
}

function MemoryList() {
  const st = useTelemetry();
  const toast = useToast();
  const serverNow = useServerNow(250);
  const [busy, setBusy] = useState<number | null>(null);
  const mem = st?.memory ?? null;
  const scene = mem?.scene ?? null;

  const aim = async (o: MemoryObject) => {
    setBusy(o.id);
    try {
      const r = await post<AimResult>("/api/memory/aim", { target: `#${o.id}` });
      toast(`Camera pointed at ${r.target}. ${r.guidance.join(" ")}`, "ok");
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
      {scene && (
        <div className="flex flex-wrap items-center gap-1 rounded-[3px] border border-synth/30 bg-synth/5 px-2 py-1">
          <span className="text-[10px] text-synth">DEMO</span>
          <button className={`chip-tab ${scene.scene === "cups" ? "active" : ""}`} onClick={() => setScene("cups")}>Cups</button>
          <button className={`chip-tab ${scene.scene === "outdoor" ? "active" : ""}`} onClick={() => setScene("outdoor")}>Outdoor</button>
          {scene.phase && <span className="w-full truncate text-[10px] text-muted">{scene.phase}</span>}
        </div>
      )}
      {objs.length === 0 && <div className="text-dim">Nothing seen yet.</div>}
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
                <button className="btn btn-sm ml-auto" disabled={busy === o.id} title="Point the camera at it" onClick={() => void aim(o)}>
                  <Crosshair size={10} /> AIM
                </button>
              </div>
              <div className="mt-0.5 flex flex-wrap gap-x-2 text-[10px] text-muted">
                {o.memory === "hidden" && <span style={{ color: MEM_COLOR.hidden }}>under {o.contained_by_name}</span>}
                {o.memory === "remembered" && <span>{o.state === "out_of_view" ? "out of view" : "not visible"}</span>}
                {o.contains.length > 0 && <span style={{ color: MEM_COLOR.hidden }}>has {o.contains.map((id) => mem.objects.find((x) => x.id === id)?.name ?? `#${id}`).join(", ")} inside</span>}
                {o.range_m != null && <span>~{o.range_m.toFixed(1)} m</span>}
              </div>
            </li>
          );
        })}
      </ul>
      {mem.events.length > 0 && (
        <ul className="space-y-0.5 border-t border-line pt-1.5 text-[10.5px]">
          {[...mem.events].reverse().slice(0, 5).map((e, i) => (
            <li key={`${e.ts}-${i}`} className="flex gap-2">
              <span className="w-10 shrink-0 text-right tabular-nums text-dim">{fmtAgo(Math.max(0, serverNow - e.ts))}</span>
              <span className={e.kind === "hidden" || e.kind === "revealed" ? "text-warn" : e.kind === "relocated" || e.kind === "not_there" ? "text-crit" : "text-muted"}>{e.text}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
