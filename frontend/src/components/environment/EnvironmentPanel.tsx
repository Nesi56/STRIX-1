import { Droplets, RotateCcw, Thermometer } from "lucide-react";
import { useEffect, useState } from "react";
import { useServerNow, useTelemetry } from "../../hooks/useTelemetry";
import { usePoll } from "../../hooks/usePoll";
import { post } from "../../services/api";
import { cToF, fmt, fmtAge, fmtDuration } from "../../lib/format";
import { Notice, Panel, StateBadge, Stat, Toggle } from "../common/ui";
import { LiveChart } from "../telemetry/LiveChart";

export function useUnitF(): [boolean, (v: boolean) => void] {
  const [f, setF] = useState(() => {
    try { return localStorage.getItem("strix.unitF") === "1"; } catch { return false; }
  });
  useEffect(() => {
    try { localStorage.setItem("strix.unitF", f ? "1" : "0"); } catch { /* private mode */ }
  }, [f]);
  return [f, setF];
}

export function EnvironmentPanel({ className, showChart = true }: { className?: string; showChart?: boolean }) {
  const st = useTelemetry();
  const env = st?.environment;
  const now = useServerNow();
  const [unitF, setUnitF] = useUnitF();
  const hist = usePoll<{ rows: { ts: number; temperature_c: number; humidity_pct: number }[] }>(showChart ? "/api/environment/history?seconds=3600" : null, 15000);
  const t = env?.latest?.temperature_c;
  const conv = (c: number | null | undefined) => (c == null ? null : unitF ? cToF(c) : c);
  const unit = unitF ? "°F" : "°C";
  const s = env?.session;
  const rows = (hist.data?.rows ?? []).map((r) => ({ ts: r.ts, temp: conv(r.temperature_c), hum: r.humidity_pct }));
  const first = rows.length ? rows[0].ts : 0;
  const failed = env && (env.status.state === "disconnected" || env.status.state === "error");
  return (
    <Panel
      title="Environment · DHT11"
      icon={<Thermometer size={14} />}
      className={className}
      badge={<StateBadge state={env?.status.state} />}
      actions={
        <>
          <Toggle checked={unitF} onChange={setUnitF} label="°F" />
          <button className="btn btn-sm btn-ghost" title="Reset session min/max" onClick={() => void post("/api/environment/reset-extremes")}>
            <RotateCcw size={12} />
          </button>
        </>
      }
    >
      {failed && <div className="mb-2"><Notice tone="crit">Sensor failure: {env?.status.error}</Notice></div>}
      {env?.status.state === "degraded" && <div className="mb-2"><Notice tone="warn">Intermittent read errors: {env.status.error}</Notice></div>}
      {env?.stale && <div className="mb-2"><Notice tone="warn">Reading is stale ({fmtAge(env.latest?.ts, now)})</Notice></div>}
      <div className="grid grid-cols-2 gap-3">
        <Stat big label="Temperature" value={fmt(conv(t), 1)} unit={unit} sub={`±${unitF ? "3.6" : "2"}${unit} sensor accuracy`} />
        <Stat big label="Humidity" value={fmt(env?.latest?.humidity_pct, 0)} unit="%RH" sub="±5 %RH sensor accuracy" />
        <Stat label="Session min / max" value={`${fmt(conv(s?.temp_min), 1)} / ${fmt(conv(s?.temp_max), 1)}`} unit={unit} />
        <Stat label="Session min / max" value={`${fmt(s?.hum_min, 0)} / ${fmt(s?.hum_max, 0)}`} unit="%RH" />
      </div>
      <div className="mt-2 flex flex-wrap gap-x-4 text-[11px] text-muted">
        <span><Droplets size={11} className="inline" /> updated {fmtAge(env?.latest?.ts, now)}</span>
        <span>poll {env?.interval_s}s</span>
        <span>reads {env?.reads ?? 0} · failures {env?.failures ?? 0}</span>
        <span>backend {env?.status.backend}</span>
      </div>
      {showChart && (
        <div className="mt-2">
          <div className="label mb-1">History · last hour</div>
          <LiveChart data={rows} xKey="ts" unit="" height={130} xFormatter={(v) => fmtDuration(v - first)}
            series={[{ key: "temp", name: `Temp ${unit}`, color: "#f2b33d" }, { key: "hum", name: "Humidity %", color: "#4c8dff" }]} />
        </div>
      )}
    </Panel>
  );
}
