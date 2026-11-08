import { Crosshair, Gauge, RotateCcw } from "lucide-react";
import { lazy, Suspense, useRef, useState } from "react";
import { useTelemetry } from "../../hooks/useTelemetry";
import { errorMessage, post } from "../../services/api";
import { cToF, fmt, signed } from "../../lib/format";
import { useToast } from "../common/toast";
import { AttitudeIndicator } from "../orientation/AttitudeIndicator";
import type { DroneSceneHandle } from "../orientation/DroneScene";

const DroneScene = lazy(() => import("../orientation/DroneScene"));

/** 3D drone driven by the MPU6050 (filtered roll/pitch, relative yaw) + attitude readouts. */
export function DronePanel() {
  const st = useTelemetry();
  const toast = useToast();
  const scene = useRef<DroneSceneHandle>(null);
  const [busy, setBusy] = useState(false);
  const imu = st?.imu;
  const s = imu?.latest;
  const env = st?.environment?.latest;
  const cal = st?.imu_calibration;
  const live = !!s && (imu?.status.state === "ok" || imu?.status.state === "synthetic");

  const calibrate = async () => {
    if (!window.confirm("Calibrate the IMU: put the drone on a level surface and keep it completely still for ~4 s. Continue?")) return;
    setBusy(true);
    try {
      await post("/api/imu/calibrate", { duration_s: 4, level: true });
      toast("IMU calibrated", "ok");
    } catch (e) {
      toast(`Calibration failed: ${errorMessage(e)}`, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel flex h-full min-h-0 flex-col">
      <header className="panel-head flex items-center gap-2 px-2.5 py-1.5">
        <h2 className="text-[10.5px] font-semibold uppercase tracking-[0.14em] text-text">Drone · IMU simulation</h2>
        <span className={`text-[10px] ${live ? "text-accent" : "text-crit"}`}>● {imu?.status.state ?? "—"}</span>
        <div className="ml-auto flex gap-1">
          <button className="btn btn-sm" onClick={() => scene.current?.resetView()} title="Reset 3D view"><Crosshair size={11} /></button>
          <button className="btn btn-sm" disabled={!live} title="Zero relative yaw"
            onClick={() => void post("/api/imu/reset-yaw").then(() => toast("Yaw zeroed", "ok")).catch((e) => toast(errorMessage(e), "error"))}><RotateCcw size={11} /></button>
          <button className="btn btn-sm btn-primary" disabled={!live || busy} onClick={() => void calibrate()}>
            <Gauge size={11} /> {busy ? "Calibrating…" : "Calibrate"}
          </button>
        </div>
      </header>
      <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_150px]">
        <div className="relative min-h-[180px]">
          <Suspense fallback={<div className="p-3 text-[11px] text-dim">Loading 3D…</div>}>
            <DroneScene ref={scene} smoothing lockYaw={false} cameraTilt={st?.servo?.tilt_deg ?? null} />
          </Suspense>
          {!live && (
            <div className="absolute inset-x-2 top-2 rounded-[3px] bg-black/75 px-2 py-1 text-[10.5px] text-crit">
              MPU6050 {imu?.status.state ?? "offline"} — the model is not moving. {imu?.status.error ?? ""}
            </div>
          )}
          <span className="absolute bottom-1 left-2 text-[9.5px] text-dim">drag to orbit · ▲ cyan nose = front · yaw is relative (no compass)</span>
        </div>
        <div className="space-y-1 border-l border-line p-2 text-[11px]">
          <div className="flex justify-center"><AttitudeIndicator roll={s?.orientation.roll ?? 0} pitch={s?.orientation.pitch ?? 0} size={92} /></div>
          <div className="flex justify-between"><span className="text-muted">ROLL</span><span>{signed(s?.orientation.roll, 1)}°</span></div>
          <div className="flex justify-between"><span className="text-muted">PITCH</span><span>{signed(s?.orientation.pitch, 1)}°</span></div>
          <div className="flex justify-between"><span className="text-muted">YAW (rel)</span><span>{signed(s?.orientation.yaw_relative, 1)}°</span></div>
          <div className="flex justify-between"><span className="text-muted">|a|</span><span>{fmt(s?.orientation.accel_norm_g, 2)} g</span></div>
          <div className="flex justify-between"><span className="text-muted">RATE</span><span>{fmt(s?.rate_hz, 0)} Hz</span></div>
          <div className="flex justify-between"><span className="text-muted">CAL</span><span className={cal?.state === "calibrated" ? "text-accent" : "text-warn"}>{cal?.state ?? "—"}</span></div>
          <div className="border-t border-line pt-1 text-muted">DHT11</div>
          <div className="flex justify-between"><span className="text-muted">TEMP</span><span>{env ? `${env.temperature_c.toFixed(1)}°C / ${cToF(env.temperature_c).toFixed(0)}°F` : "—"}</span></div>
          <div className="flex justify-between"><span className="text-muted">HUMIDITY</span><span>{env ? `${env.humidity_pct.toFixed(0)} %` : "—"}</span></div>
        </div>
      </div>
    </section>
  );
}
