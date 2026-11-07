import { Activity } from "lucide-react";
import { useImuSeries, useTelemetry } from "../../hooks/useTelemetry";
import { fmt, signed } from "../../lib/format";
import { Panel, StateBadge, Stat, Tag } from "../common/ui";
import { AXIS_COLORS, LiveChart } from "./LiveChart";

export function ImuReadouts() {
  const st = useTelemetry();
  const s = st?.imu.latest;
  return (
    <div className="grid grid-cols-3 gap-x-4 gap-y-2 sm:grid-cols-6">
      <Stat label="Accel X" value={signed(s?.accel.x, 2)} unit="m/s²" />
      <Stat label="Accel Y" value={signed(s?.accel.y, 2)} unit="m/s²" />
      <Stat label="Accel Z" value={signed(s?.accel.z, 2)} unit="m/s²" />
      <Stat label="Gyro X" value={signed(s?.gyro.x, 2)} unit="°/s" />
      <Stat label="Gyro Y" value={signed(s?.gyro.y, 2)} unit="°/s" />
      <Stat label="Gyro Z" value={signed(s?.gyro.z, 2)} unit="°/s" />
      <Stat label="Roll (est.)" value={signed(s?.orientation.roll, 1)} unit="°" />
      <Stat label="Pitch (est.)" value={signed(s?.orientation.pitch, 1)} unit="°" />
      <Stat label="Rel. yaw" value={signed(s?.orientation.yaw_relative, 1)} unit="°" sub="drifts · not heading" />
      <Stat label="IMU temp" value={fmt(s?.temp_c, 1)} unit="°C" sub="die temperature" />
      <Stat label="Sample rate" value={fmt(s?.rate_hz, 0)} unit="Hz" />
      <Stat label="Timestamp" value={s ? new Date(s.ts * 1000).toLocaleTimeString([], { hour12: false }) : "—"} sub={s ? `seq ${s.seq}` : undefined} />
    </div>
  );
}

export function TelemetryPanel({ className, windowS = 30, chartHeight = 150 }: { className?: string; windowS?: number; chartHeight?: number }) {
  const st = useTelemetry();
  const data = useImuSeries(windowS);
  const last = data.length ? data[data.length - 1].t : 0;
  const rel = (v: number) => `${(v - last).toFixed(0)}s`;
  const health = st?.imu_health ?? [];
  return (
    <Panel
      title="Sensor Telemetry · MPU6050"
      icon={<Activity size={14} />}
      className={className}
      badge={<StateBadge state={st?.imu.status.state} />}
      actions={
        <>
          {health.filter((h) => h.severity !== "info").map((h) => (
            <Tag key={h.code} tone={h.severity === "critical" ? "crit" : "warn"} title={h.message}>{h.code.toUpperCase()}</Tag>
          ))}
          {st?.imu.latest?.saturated && <Tag tone="crit">SATURATED</Tag>}
        </>
      }
    >
      <ImuReadouts />
      <div className="mt-3 grid gap-3 lg:grid-cols-2">
        <div>
          <div className="label mb-1">Acceleration (m/s²) · last {windowS}s</div>
          <LiveChart data={data} xKey="t" unit="m/s²" height={chartHeight} xFormatter={rel}
            series={[{ key: "ax", name: "X", color: AXIS_COLORS.x }, { key: "ay", name: "Y", color: AXIS_COLORS.y }, { key: "az", name: "Z", color: AXIS_COLORS.z }]} />
        </div>
        <div>
          <div className="label mb-1">Angular velocity (°/s) · last {windowS}s</div>
          <LiveChart data={data} xKey="t" unit="°/s" height={chartHeight} xFormatter={rel}
            series={[{ key: "gx", name: "X", color: AXIS_COLORS.x }, { key: "gy", name: "Y", color: AXIS_COLORS.y }, { key: "gz", name: "Z", color: AXIS_COLORS.z }]} />
        </div>
      </div>
      {health.length > 0 && (
        <ul className="mt-2 space-y-0.5 text-[11px]">
          {health.map((h) => (
            <li key={h.code} className={h.severity === "critical" ? "text-crit" : h.severity === "warning" ? "text-warn" : "text-muted"}>
              ● {h.message}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

export function OrientationChart({ windowS = 60 }: { windowS?: number }) {
  const data = useImuSeries(windowS);
  const last = data.length ? data[data.length - 1].t : 0;
  return (
    <LiveChart data={data} xKey="t" unit="°" height={170} xFormatter={(v) => `${(v - last).toFixed(0)}s`}
      series={[{ key: "roll", name: "Roll", color: "#38d6ff" }, { key: "pitch", name: "Pitch", color: "#f2b33d" }, { key: "yaw", name: "Rel. yaw", color: "#c08bff" }]} />
  );
}
