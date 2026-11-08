import { Crosshair, Gauge, RotateCcw } from "lucide-react";
import { lazy, Suspense, useRef, useState } from "react";
import { useServerNow, useTelemetry } from "../../hooks/useTelemetry";
import { MEM_COLOR } from "../../lib/memory";
import { errorMessage, post } from "../../services/api";
import { cToF, signed } from "../../lib/format";
import { useToast } from "../common/toast";
import { AttitudeIndicator } from "../orientation/AttitudeIndicator";
import type { DroneSceneHandle } from "../orientation/DroneScene";

const DroneScene = lazy(() => import("../orientation/DroneScene"));
const WorldScene = lazy(() => import("../memory/WorldScene"));

type View = "world" | "drone";

/** Live 3D: the drone (MPU6050 attitude, camera tilt) in its object memory — or a close-up of the drone. */
export function DronePanel() {
  const st = useTelemetry();
  const toast = useToast();
  const scene = useRef<DroneSceneHandle>(null);
  const [busy, setBusy] = useState(false);
  const [view, setView] = useState<View>("world");
  const [fitNonce, setFitNonce] = useState(0);
  const serverNow = useServerNow(500);
  const mem = st?.memory ?? null;
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
        <h2 className="text-[10.5px] font-semibold uppercase tracking-[0.14em] text-text">Live 3D</h2>
        <div className="flex gap-1">
          <button className={`chip-tab ${view === "world" ? "active" : ""}`} onClick={() => setView("world")}>World</button>
          <button className={`chip-tab ${view === "drone" ? "active" : ""}`} onClick={() => setView("drone")}>Drone</button>
        </div>
        <div className="ml-auto flex gap-1">
          <button className="btn btn-sm" onClick={() => (view === "world" ? setFitNonce((n) => n + 1) : scene.current?.resetView())} title="Reset 3D view"><Crosshair size={11} /></button>
          <button className="btn btn-sm" disabled={!live} title="Zero relative yaw"
            onClick={() => void post("/api/imu/reset-yaw").then(() => toast("Yaw zeroed", "ok")).catch((e) => toast(errorMessage(e), "error"))}><RotateCcw size={11} /></button>
          <button className="btn btn-sm btn-primary" disabled={!live || busy} onClick={() => void calibrate()} title="Calibrate the IMU (keep the drone still and level)">
            <Gauge size={11} />{busy ? " …" : ""}
          </button>
        </div>
      </header>
      <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_112px]">
        <div className="relative min-h-[180px]">
          <Suspense fallback={<div className="p-3 text-[11px] text-dim">Loading 3D…</div>}>
            {view === "world"
              ? <WorldScene mem={mem} serverNow={serverNow} fitNonce={fitNonce} />
              : <DroneScene ref={scene} smoothing lockYaw={false} cameraTilt={st?.servo?.tilt_deg ?? null} />}
          </Suspense>
          {!live && (
            <div className="absolute inset-x-2 top-2 rounded-[3px] bg-black/75 px-2 py-1 text-[10.5px] text-crit">
              MPU6050 {imu?.status.state ?? "offline"}: {imu?.status.error ?? "no data"}
            </div>
          )}
          {view === "world" && (
            <div className="pointer-events-none absolute left-2 top-2 flex flex-wrap gap-x-2.5 gap-y-0.5 rounded-[3px] bg-black/60 px-2 py-1 text-[10px]">
              <span style={{ color: MEM_COLOR.seen }}>■ {mem?.counts.seen ?? 0} seen</span>
              <span style={{ color: MEM_COLOR.remembered }}>□ {mem?.counts.remembered ?? 0} remembered</span>
              <span style={{ color: MEM_COLOR.hidden }}>◆ {mem?.counts.hidden ?? 0} hidden</span>
            </div>
          )}

        </div>
        <div className="space-y-1 border-l border-line p-2 text-[11px]">
          <div className="flex justify-center"><AttitudeIndicator roll={s?.orientation.roll ?? 0} pitch={s?.orientation.pitch ?? 0} size={70} /></div>
          <div className="flex justify-between"><span className="text-muted">ROLL</span><span>{signed(s?.orientation.roll, 1)}°</span></div>
          <div className="flex justify-between"><span className="text-muted">PITCH</span><span>{signed(s?.orientation.pitch, 1)}°</span></div>
          <div className="flex justify-between"><span className="text-muted">YAW</span><span>{signed(s?.orientation.yaw_relative, 1)}°</span></div>
          {cal?.state !== "calibrated" && <div className="text-[10px] text-warn">not calibrated</div>}
          <div className="border-t border-line pt-1" />
          <div className="flex justify-between"><span className="text-muted">TEMP</span><span title={env ? `${cToF(env.temperature_c).toFixed(0)} °F` : undefined}>{env ? `${env.temperature_c.toFixed(1)}°C` : "—"}</span></div>
          <div className="flex justify-between"><span className="text-muted">HUMID</span><span>{env ? `${env.humidity_pct.toFixed(0)} %` : "—"}</span></div>
        </div>
      </div>
    </section>
  );
}
