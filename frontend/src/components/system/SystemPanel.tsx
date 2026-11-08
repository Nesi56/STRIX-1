import { BatteryWarning, Cpu, HardDrive, MemoryStick, Thermometer, Wifi, Zap } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTelemetry, useTelemetrySnapshot } from "../../hooks/useTelemetry";
import { fmt, fmtBytes, fmtDuration, fmtRate } from "../../lib/format";
import { Bar, Notice, Panel, Stat, Tag } from "../common/ui";
import { LiveChart } from "../telemetry/LiveChart";

function useCpuHistory() {
  const sys = useTelemetry()?.system;
  const ref = useRef<{ t: number; cpu: number; temp: number | null }[]>([]);
  const [data, setData] = useState<typeof ref.current>([]);
  useEffect(() => {
    if (!sys) return;
    const last = ref.current[ref.current.length - 1];
    if (last && last.t === sys.ts) return;
    ref.current = [...ref.current, { t: sys.ts, cpu: sys.cpu_percent, temp: sys.cpu_temp_c }].slice(-300);
    setData(ref.current);
  }, [sys]);
  return data;
}

export function SystemPanel({ className, detailed = false }: { className?: string; detailed?: boolean }) {
  const snap = useTelemetrySnapshot();
  const st = snap.state;
  const sys = st?.system;
  const hist = useCpuHistory();
  if (!sys) return <Panel title="System Health" icon={<Cpu size={14} />} className={className}><div className="text-muted">Waiting for data…</div></Panel>;
  const p = sys.power;
  const thr = p?.throttled ?? null;
  const wifi = Object.entries(sys.network.wireless ?? {})[0];
  const temp = sys.cpu_temp_c;
  const qwenProc = sys.processes["llama-server"];
  const ai = st?.ai;
  const first = hist[0]?.t ?? 0;
  return (
    <Panel title="System Health" icon={<Cpu size={14} />} className={className}
      badge={<span className="num text-[10px] text-muted">{sys.pi_model ?? sys.hostname}</span>}
      actions={sys.warnings.length ? <Tag tone={sys.warnings.some((w) => w.severity === "critical") ? "crit" : "warn"}>{sys.warnings.length} WARNING{sys.warnings.length > 1 ? "S" : ""}</Tag> : <Tag tone="ok">NOMINAL</Tag>}>
      {sys.warnings.length > 0 && (
        <div className="mb-2 space-y-1">{sys.warnings.map((w) => <Notice key={w.code} tone={w.severity === "critical" ? "crit" : "warn"}>{w.message}</Notice>)}</div>
      )}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div className="space-y-1"><Stat label="CPU" value={fmt(sys.cpu_percent, 0)} unit="%" sub={`${sys.cpu_count} cores · ${fmt(sys.cpu_freq_mhz, 0)} MHz`} /><Bar value={sys.cpu_percent} /></div>
        <div className="space-y-1"><Stat label="RAM" value={fmt(sys.memory.used_percent, 0)} unit="%" sub={`${fmt(sys.memory.available_mb / 1000, 1)} GB free of ${fmt(sys.memory.total_mb / 1000, 1)}`} /><Bar value={sys.memory.used_percent} /></div>
        <div className="space-y-1">
          <Stat label="CPU temp" value={fmt(temp, 1)} unit="°C" tone={temp != null && temp >= 80 ? "crit" : temp != null && temp >= 70 ? "warn" : "default"} sub={`load ${sys.load_avg.map((l) => l.toFixed(2)).join(" ")}`} />
          <Bar value={temp ?? 0} max={90} />
        </div>
        <div className="space-y-1">
          <Stat label="Link RTT" value={snap.rttMs != null ? snap.rttMs.toFixed(0) : "—"} unit="ms" sub={wifi ? `${wifi[0]} ${wifi[1].signal_dbm} dBm` : "no Wi-Fi stats"} />
          <Bar value={snap.rttMs ?? 0} max={300} />
        </div>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-3 text-[12px] sm:grid-cols-4">
        <div>
          <div className="label flex items-center gap-1"><Zap size={10} />Power</div>
          <div className="num">{p?.input_voltage_v != null ? `${p.input_voltage_v.toFixed(2)} V in` : "input V n/a"}</div>
          <div className="num text-muted">{p?.pmic?.rail_power_w != null ? `≈${p.pmic.rail_power_w.toFixed(2)} W SoC rails` : "rail power n/a"}</div>
          <div className={p?.undervoltage_now ? "text-crit" : p?.undervoltage_occurred ? "text-warn" : "text-muted"}>
            {p?.undervoltage_now == null ? "undervoltage: not measurable" : p.undervoltage_now ? "UNDER-VOLTAGE NOW" : p.undervoltage_occurred ? "under-voltage occurred" : "no under-voltage"}
          </div>
        </div>
        <div>
          <div className="label flex items-center gap-1"><BatteryWarning size={10} />Battery</div>
          <div className="text-muted">{p?.battery?.available ? `${p.battery.percent}%` : "Unavailable"}</div>
          <div className="text-[10.5px] text-dim">{p?.battery?.reason ?? "no battery telemetry"}</div>
        </div>
        <div>
          <div className="label flex items-center gap-1"><Wifi size={10} />Network</div>
          <div className="num">↓ {fmtRate(sys.network.rx_bps)}</div>
          <div className="num">↑ {fmtRate(sys.network.tx_bps)}</div>
        </div>
        <div>
          <div className="label flex items-center gap-1"><HardDrive size={10} />Storage</div>
          <div className="num">{sys.disk ? `${sys.disk.free_gb.toFixed(1)} GB free` : "—"}</div>
          <div className="num text-muted">DB {fmtBytes(st?.recording?.db_bytes)}</div>
        </div>
      </div>
      <div className="mt-3 grid grid-cols-1 gap-3 text-[12px] sm:grid-cols-2">
        <div>
          <div className="label flex items-center gap-1"><MemoryStick size={10} />AI inference</div>
          <div className="num">Qwen: {ai?.qwen.state ?? "—"}{ai?.qwen.last?.tokens_per_s != null ? ` · ${ai.qwen.last.tokens_per_s.toFixed(1)} tok/s · ${((ai.qwen.last.latency_ms ?? 0) / 1000).toFixed(1)} s` : ""}</div>
          {qwenProc && <div className="num text-muted">llama-server {qwenProc.rss_mb.toFixed(0)} MB · {qwenProc.cpu_percent.toFixed(0)}% CPU</div>}
          <div className="num text-muted">YOLO: {st?.detector ? `${st.detector.fps.toFixed(1)} fps · ${st.detector.infer_ms?.toFixed(0) ?? "—"} ms · ${st.detector.engine}` : "—"}</div>
        </div>
        <div>
          <div className="label flex items-center gap-1"><Thermometer size={10} />Throttling</div>
          {thr ? (
            <div className="flex flex-wrap gap-1">
              {(["under_voltage_now", "throttled_now", "arm_freq_capped_now", "soft_temp_limit_now"] as const).map((k) => (
                <Tag key={k} tone={thr[k] ? "crit" : "muted"}>{k.replace(/_now$/, "").replace(/_/g, " ")}</Tag>
              ))}
              <span className="num text-[10px] text-dim">{String(thr.raw)}</span>
            </div>
          ) : <div className="text-muted">vcgencmd not available</div>}
        </div>
      </div>
      {detailed && (
        <>
          <div className="mt-3">
            <div className="label mb-1">Per-core load</div>
            <div className="grid grid-cols-4 gap-2">{sys.cpu_per_core.map((c, i) => <div key={i}><div className="num text-[10px] text-muted">core {i} · {c.toFixed(0)}%</div><Bar value={c} /></div>)}</div>
          </div>
          <div className="mt-3">
            <div className="label mb-1">CPU load & temperature (session)</div>
            <LiveChart data={hist} xKey="t" unit="" height={150} xFormatter={(v) => fmtDuration(v - first)}
              series={[{ key: "cpu", name: "CPU %", color: "#38d6ff" }, { key: "temp", name: "Temp °C", color: "#f2b33d" }]} />
          </div>
          <div className="mt-3">
            <div className="label mb-1">Processes</div>
            <table className="num w-full text-[11px]">
              <tbody>
                {Object.entries(sys.processes).map(([k, v]) => (
                  <tr key={k} className="border-b border-line"><td className="py-1">{k}</td><td>pid {v.pid}</td><td>{v.cpu_percent.toFixed(0)}% CPU</td><td>{v.rss_mb.toFixed(0)} MB</td></tr>
                ))}
              </tbody>
            </table>
            <div className="mt-1 text-[11px] text-muted">uptime {fmtDuration(sys.uptime_s)} · swap {fmt(sys.memory.swap_used_percent, 0)}%</div>
          </div>
        </>
      )}
    </Panel>
  );
}
