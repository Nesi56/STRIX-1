import { Cog, Gauge, LogOut, Move, ShieldCheck, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useTelemetry } from "../../hooks/useTelemetry";
import { usePoll } from "../../hooks/usePoll";
import { del, errorMessage, post } from "../../services/api";
import { fmt, fmtTime } from "../../lib/format";
import { Notice, Panel, StateBadge, Tag, Toggle } from "../common/ui";
import { useToast } from "../common/toast";
import { useUnitF } from "../environment/EnvironmentPanel";

interface CalStatus {
  state: string;
  message?: string;
  calibration: { gyro_bias_dps: number[]; accel_offset_ms2: number[]; gyro_noise_dps: number | null; accel_noise_ms2: number | null; calibrated_at: number | null; samples: number; level_reference: boolean };
  yaw_integrated_s: number;
  yaw_drift_bound_deg: number | null;
}

function ImuCalibration() {
  const toast = useToast();
  const { data, refresh } = usePoll<CalStatus>("/api/imu/calibration", 3000);
  const [duration, setDuration] = useState(4);
  const [level, setLevel] = useState(true);
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true);
    try {
      await post("/api/imu/calibrate", { duration_s: duration, level });
      toast("Calibration complete", "ok");
    } catch (e) {
      toast(`Calibration failed: ${errorMessage(e)}`, "error");
    } finally {
      setBusy(false);
      void refresh();
    }
  };
  const c = data?.calibration;
  const v3 = (v?: number[], d = 3) => (v ? v.map((x) => x.toFixed(d)).join(" / ") : "—");
  return (
    <Panel title="IMU Calibration" icon={<Gauge size={14} />}
      badge={<Tag tone={data?.state === "calibrated" ? "ok" : data?.state === "failed" ? "crit" : "warn"}>{(data?.state ?? "—").toUpperCase()}</Tag>}>
      <ol className="mb-3 ml-4 list-decimal space-y-0.5 text-[12px] text-muted">
        <li>Place the airframe on a level, vibration-free surface (motors off, props removed).</li>
        <li>Keep it completely still for the whole capture.</li>
        <li>Calibration measures gyro bias (all axes) and, if "level reference" is on, accelerometer offsets assuming level.</li>
      </ol>
      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-1.5 text-[12px]">Duration
          <select className="select" value={duration} onChange={(e) => setDuration(Number(e.target.value))}>{[2, 4, 8, 15].map((d) => <option key={d} value={d}>{d} s</option>)}</select>
        </label>
        <Toggle checked={level} onChange={setLevel} label="Level reference (accel offsets)" />
        <button className="btn btn-primary" onClick={run} disabled={busy}>{busy ? "Capturing…" : "Run calibration"}</button>
        <button className="btn" onClick={() => void post("/api/imu/reset-yaw").then(() => toast("Relative yaw zeroed", "ok"))}>Zero relative yaw</button>
        <button className="btn btn-danger" onClick={() => void del("/api/imu/calibration").then(refresh)}><Trash2 size={12} /> Clear</button>
      </div>
      {data?.state === "failed" && <div className="mt-2"><Notice tone="crit">{data.message}</Notice></div>}
      <div className="num mt-3 grid grid-cols-1 gap-x-6 gap-y-1 text-[12px] sm:grid-cols-2">
        <div><span className="text-muted">Calibrated at </span>{fmtTime(c?.calibrated_at, true)}</div>
        <div><span className="text-muted">Samples </span>{c?.samples ?? 0}</div>
        <div><span className="text-muted">Gyro bias °/s </span>{v3(c?.gyro_bias_dps)}</div>
        <div><span className="text-muted">Accel offset m/s² </span>{v3(c?.accel_offset_ms2)}</div>
        <div><span className="text-muted">Gyro noise σ </span>{fmt(c?.gyro_noise_dps, 3, " °/s")}</div>
        <div><span className="text-muted">Accel noise σ </span>{fmt(c?.accel_noise_ms2, 3, " m/s²")}</div>
        <div><span className="text-muted">Yaw integrated </span>{fmt(data?.yaw_integrated_s, 0, " s")}</div>
        <div><span className="text-muted">Yaw drift bound </span><span className="text-warn">{data?.yaw_drift_bound_deg != null ? `±${data.yaw_drift_bound_deg.toFixed(1)}°` : "unknown (uncalibrated)"}</span></div>
      </div>
      <p className="mt-2 text-[11px] text-muted">The MPU6050 has no magnetometer: yaw is integrated gyro rate and drifts without bound; the bound shown is an indicator, not a guarantee.</p>
    </Panel>
  );
}

function ServoLimits() {
  const sv = useTelemetry()?.servo;
  const toast = useToast();
  const [form, setForm] = useState({ min_angle: 30, max_angle: 150, center_angle: 90, max_speed_dps: 60 });
  useEffect(() => {
    if (sv) setForm({ min_angle: sv.limits.min_angle, max_angle: sv.limits.max_angle, center_angle: sv.limits.center_angle, max_speed_dps: sv.limits.max_speed_dps });
    // Re-sync the form only when the saved limits change.
  }, [sv?.limits.min_angle, sv?.limits.max_angle, sv?.limits.center_angle, sv?.limits.max_speed_dps]);
  const save = async () => {
    try {
      await post("/api/servo/limits", form);
      toast("Servo limits saved", "ok");
    } catch (e) {
      toast(errorMessage(e), "error");
    }
  };
  const field = (k: keyof typeof form, label: string, min: number, max: number) => (
    <label className="flex flex-col gap-0.5 text-[11px]"><span className="label">{label}</span>
      <input type="number" className="input w-24" min={min} max={max} value={form[k]} onChange={(e) => setForm({ ...form, [k]: Number(e.target.value) })} />
    </label>
  );
  return (
    <Panel title="Camera Servo Limits" icon={<Move size={14} />} badge={<StateBadge state={sv?.status.state} />}>
      <div className="flex flex-wrap items-end gap-3">
        {field("min_angle", "Safe min °", 0, 180)}
        {field("max_angle", "Safe max °", 0, 180)}
        {field("center_angle", "Center °", 0, 180)}
        {field("max_speed_dps", "Max speed °/s", 5, 360)}
        <button className="btn btn-primary" onClick={save}>Save limits</button>
        {sv?.armed && <button className="btn btn-danger" onClick={() => void post("/api/servo/disarm")}>Disable servo control</button>}
      </div>
      <p className="mt-2 text-[11px] text-muted">
        Limits are clamped inside the 0–180° mechanical range and persisted on the Pi. Verify the SG90 end-stops and the camera mount before widening them.
        Pulse range {sv?.pulse_range_us[0]}–{sv?.pulse_range_us[1]} µs is configured in <code>.env</code>.
      </p>
    </Panel>
  );
}

const HW_ROWS: { key: string; feature: string; how: string }[] = [
  { key: "imu", feature: "MPU6050 over I2C bus 1 (GPIO2/3)", how: "scripts/verify_imu.py" },
  { key: "environment", feature: "DHT11 on GPIO4 (kernel IIO overlay)", how: "scripts/verify_dht.py" },
  { key: "camera", feature: "Camera Module 3 Wide (Picamera2)", how: "scripts/verify_camera.sh" },
  { key: "servo", feature: "SG90 on GPIO18 (hardware PWM / gpiozero)", how: "scripts/verify_servo.py" },
];

function HardwareStatus() {
  const st = useTelemetry();
  const state = (k: string) => (k === "imu" ? st?.imu.status : k === "environment" ? st?.environment?.status : k === "camera" ? st?.camera?.status : st?.servo?.status);
  return (
    <Panel title="Hardware Verification" icon={<ShieldCheck size={14} />}>
      <table className="w-full text-[12px]">
        <tbody>
          {HW_ROWS.map((r) => {
            const s = state(r.key);
            return (
              <tr key={r.key} className="border-b border-line">
                <td className="py-1.5 pr-2">{r.feature}</td>
                <td className="pr-2"><StateBadge state={s?.state} /></td>
                <td className="num pr-2 text-[11px] text-muted">{s?.backend}</td>
                <td className="num text-[11px] text-dim">{r.how}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="mt-2 text-[11px] text-muted">"SYNTHETIC" means demo data from the mock backend, never a real measurement. See docs/HARDWARE_VERIFICATION.md for the full checklist.</p>
    </Panel>
  );
}

function ConfigView() {
  const { data } = usePoll<Record<string, unknown>>("/api/config", 0);
  const [unitF, setUnitF] = useUnitF();
  const logout = async () => {
    await post("/api/auth/logout").catch(() => {});
    location.reload();
  };
  return (
    <Panel title="Configuration" icon={<Cog size={14} />} actions={<button className="btn btn-sm" onClick={logout}><LogOut size={11} /> Sign out</button>}>
      <div className="mb-2 flex items-center gap-3"><Toggle checked={unitF} onChange={setUnitF} label="Show temperature in °F" /></div>
      <p className="mb-2 text-[11px] text-muted">Read-only view of the backend configuration (secrets are never returned). Change values in <code>.env</code> on the Pi and restart the service.</p>
      <pre className="num max-h-80 overflow-auto rounded border border-line bg-[#050a13] p-2 text-[11px] leading-relaxed text-[#a9bfd9]">{data ? JSON.stringify(data, null, 2) : "…"}</pre>
    </Panel>
  );
}

export function SettingsPanel() {
  return (
    <div className="grid gap-3 xl:grid-cols-2">
      <ImuCalibration />
      <HardwareStatus />
      <ServoLimits />
      <ConfigView />
    </div>
  );
}
