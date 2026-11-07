import { Crosshair, Gauge, RotateCcw, Rotate3d } from "lucide-react";
import { lazy, Suspense, useRef, useState } from "react";
import { useTelemetry } from "../../hooks/useTelemetry";
import { errorMessage, post } from "../../services/api";
import { fmt, signed } from "../../lib/format";
import { Empty, Panel, StateBadge, Tag, Toggle } from "../common/ui";
import { useToast } from "../common/toast";
import { AttitudeIndicator } from "./AttitudeIndicator";
import type { DroneSceneHandle } from "./DroneScene";

const DroneScene = lazy(() => import("./DroneScene"));

export function OrientationPanel({ className }: { className?: string }) {
  const st = useTelemetry();
  const imu = st?.imu;
  const s = imu?.latest;
  const cal = st?.imu_calibration;
  const toast = useToast();
  const scene = useRef<DroneSceneHandle>(null);
  const [smoothing, setSmoothing] = useState(true);
  const [lockYaw, setLockYaw] = useState(false);
  const [calibrating, setCalibrating] = useState(false);
  const servo = st?.servo;
  const tilt = servo?.commanded_angle != null ? servo.commanded_angle - (servo.limits.center_angle ?? 90) : null;

  const recalibrate = async () => {
    if (!window.confirm("IMU calibration: place the drone on a level surface and keep it completely still for ~4 s. Continue?")) return;
    setCalibrating(true);
    try {
      await post("/api/imu/calibrate", { duration_s: 4, level: true });
      toast("IMU calibrated (gyro bias + level reference)", "ok");
    } catch (e) {
      toast(`Calibration failed: ${errorMessage(e)}`, "error");
    } finally {
      setCalibrating(false);
    }
  };
  const resetYaw = async () => {
    try {
      await post("/api/imu/reset-yaw");
      toast("Relative yaw reset to 0°", "ok");
    } catch (e) {
      toast(errorMessage(e), "error");
    }
  };

  return (
    <Panel
      title="3D Orientation"
      icon={<Rotate3d size={14} />}
      className={className}
      badge={<StateBadge state={imu?.status.state} />}
      actions={<Tag tone="muted" title="Complementary filter estimate from accelerometer + gyroscope">ESTIMATED</Tag>}
      bodyClassName="!p-0 flex flex-col"
    >
      <div className="relative min-h-[220px] flex-1">
        {s ? (
          <Suspense fallback={<Empty>Loading 3D view…</Empty>}>
            <DroneScene ref={scene} smoothing={smoothing} lockYaw={lockYaw} cameraTilt={tilt} />
          </Suspense>
        ) : (
          <Empty>{imu?.status.error ?? "Waiting for IMU data…"}</Empty>
        )}
        {s && (
          <>
            <div className="pointer-events-none absolute left-2 top-2">
              <AttitudeIndicator roll={s.orientation.roll} pitch={s.orientation.pitch} size={118} />
            </div>
            <div className="num pointer-events-none absolute right-2 top-2 grid grid-cols-[auto_auto] gap-x-3 rounded bg-black/55 px-2 py-1.5 text-[12px]">
              <span className="text-muted">ROLL</span><span className="text-right">{signed(s.orientation.roll, 1, "°")}</span>
              <span className="text-muted">PITCH</span><span className="text-right">{signed(s.orientation.pitch, 1, "°")}</span>
              <span className="text-muted">REL YAW</span><span className="text-right">{signed(s.orientation.yaw_relative, 1, "°")}</span>
              <span className="text-muted">YAW DRIFT</span>
              <span className="text-right text-warn">{cal?.yaw_drift_bound_deg != null ? `±${fmt(cal.yaw_drift_bound_deg, 1)}°` : "unbounded"}</span>
              <span className="text-muted">ACC TRUST</span><span className="text-right">{fmt(s.orientation.accel_trust * 100, 0, "%")}</span>
              <span className="text-muted">|a|</span><span className="text-right">{fmt(s.orientation.accel_norm_g, 3, " g")}</span>
            </div>
            <div className="pointer-events-none absolute bottom-2 left-2 max-w-[70%] rounded bg-black/55 px-2 py-1 text-[10px] leading-snug text-muted">
              <span className="text-cyan">▲ nose / cyan props = front</span> · red props = rear · estimated orientation, roll/pitch gravity-referenced ·{" "}
              <span className="text-warn">yaw is relative (no magnetometer) — not a compass heading</span>
            </div>
          </>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-1.5 border-t border-line px-2 py-1.5">
        <button className="btn btn-sm" onClick={() => scene.current?.resetView()} disabled={!s}><Crosshair size={12} /> Reset view</button>
        <button className="btn btn-sm" onClick={resetYaw} disabled={!s}><RotateCcw size={12} /> Zero yaw</button>
        <button className="btn btn-sm btn-primary" onClick={recalibrate} disabled={!s || calibrating}>
          <Gauge size={12} /> {calibrating ? "Calibrating…" : "Recalibrate IMU"}
        </button>
        <div className="ml-auto flex items-center gap-3">
          <Toggle checked={smoothing} onChange={setSmoothing} label="Smooth (40 ms)" />
          <Toggle checked={lockYaw} onChange={setLockYaw} label="Lock yaw" />
          <Tag tone={cal?.state === "calibrated" ? "ok" : "warn"}>{(cal?.state ?? "uncalibrated").toUpperCase()}</Tag>
        </div>
      </div>
    </Panel>
  );
}
