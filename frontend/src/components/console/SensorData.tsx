import { useImuSeries, useServerNow, useTelemetry } from "../../hooks/useTelemetry";
import { cToF, fmt, fmtAge, signed } from "../../lib/format";
import { AttitudeIndicator } from "../orientation/AttitudeIndicator";
import { LiveChart } from "../telemetry/LiveChart";

function Row({ k, v, tone }: { k: string; v: string; tone?: string }) {
  return (
    <div className="flex justify-between gap-2 border-b border-line/60 py-[2px]">
      <span className="text-muted">{k}</span>
      <span className={tone ?? "text-text"}>{v}</span>
    </div>
  );
}

export function SensorData() {
  const st = useTelemetry();
  const now = useServerNow();
  const s = st?.imu.latest;
  const env = st?.environment;
  const sys = st?.system;
  const data = useImuSeries(30, 500);
  const last = data.length ? data[data.length - 1].t : 0;
  const health = st?.imu_health ?? [];
  return (
    <div className="space-y-2 text-[11px]">
      <div className="flex gap-2">
        {s && <AttitudeIndicator roll={s.orientation.roll} pitch={s.orientation.pitch} size={96} />}
        <div className="min-w-0 flex-1">
          <div className="mb-0.5 text-[10px] font-semibold tracking-[0.14em] text-accent">MPU6050 · {st?.imu.status.state.toUpperCase()}</div>
          <Row k="roll / pitch" v={`${signed(s?.orientation.roll, 1)}° / ${signed(s?.orientation.pitch, 1)}°`} />
          <Row k="relative yaw" v={`${signed(s?.orientation.yaw_relative, 1)}° (drifts)`} />
          <Row k="accel m/s²" v={s ? `${s.accel.x.toFixed(2)} ${s.accel.y.toFixed(2)} ${s.accel.z.toFixed(2)}` : "—"} />
          <Row k="gyro °/s" v={s ? `${s.gyro.x.toFixed(1)} ${s.gyro.y.toFixed(1)} ${s.gyro.z.toFixed(1)}` : "—"} />
          <Row k="rate · die temp" v={`${fmt(s?.rate_hz, 0)} Hz · ${fmt(s?.temp_c, 1)} °C`} />
        </div>
      </div>
      {health.filter((h) => h.severity !== "info").map((h) => <div key={h.code} className="text-warn">⚠ {h.message}</div>)}
      <LiveChart data={data} xKey="t" unit="°" height={110} xFormatter={(v) => `${(v - last).toFixed(0)}s`}
        series={[{ key: "roll", name: "roll", color: "#3ddc84" }, { key: "pitch", name: "pitch", color: "#4aa3ff" }]} />
      <div className="grid grid-cols-2 gap-x-3">
        <div>
          <div className="mb-0.5 text-[10px] font-semibold tracking-[0.14em] text-accent">DHT11 · {env?.status.state.toUpperCase() ?? "—"}</div>
          <Row k="temperature" v={env?.latest ? `${env.latest.temperature_c.toFixed(1)} °C / ${cToF(env.latest.temperature_c).toFixed(0)} °F` : "—"} />
          <Row k="humidity" v={env?.latest ? `${env.latest.humidity_pct.toFixed(0)} %RH` : "—"} />
          <Row k="updated" v={fmtAge(env?.latest?.ts, now)} tone={env?.stale ? "text-warn" : undefined} />
          <Row k="min / max" v={env?.session.temp_min != null ? `${env.session.temp_min.toFixed(1)} / ${env.session.temp_max?.toFixed(1)} °C` : "—"} />
        </div>
        <div>
          <div className="mb-0.5 text-[10px] font-semibold tracking-[0.14em] text-accent">SERVO · SYSTEM</div>
          <Row k="camera tilt" v={`${signed(st?.servo?.tilt_deg, 0)}° ${st?.servo?.armed ? "(armed)" : "(safe)"}`} />
          <Row k="CPU / RAM" v={`${fmt(sys?.cpu_percent, 0)}% / ${fmt(sys?.memory.used_percent, 0)}%`} />
          <Row k="SoC temp" v={fmt(sys?.cpu_temp_c, 1, " °C")} />
          <Row k="power" v={sys?.power?.undervoltage_now ? "UNDER-VOLTAGE" : sys?.power?.input_voltage_v != null ? `${sys.power.input_voltage_v.toFixed(2)} V` : "n/a"}
            tone={sys?.power?.undervoltage_now ? "text-crit" : undefined} />
        </div>
      </div>
      {env?.status.error && env.status.state !== "synthetic" && <div className="text-warn">DHT11: {env.status.error}</div>}
      <p className="text-dim">Battery level: unavailable (no battery telemetry). Yaw is relative — the MPU6050 has no magnetometer.</p>
    </div>
  );
}
