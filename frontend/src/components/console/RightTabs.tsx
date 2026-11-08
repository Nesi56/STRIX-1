import { lazy, Suspense, useRef, useState } from "react";
import { useTelemetry } from "../../hooks/useTelemetry";
import { Empty } from "../common/ui";
import type { DroneSceneHandle } from "../orientation/DroneScene";
import { AgentGraph } from "./AgentGraph";
import { RadarView } from "./RadarView";
import { SensorData } from "./SensorData";

const DroneScene = lazy(() => import("../orientation/DroneScene"));
type Tab = "graph" | "radar" | "sensors" | "3d";
const TABS: { id: Tab; label: string }[] = [
  { id: "graph", label: "Agent Graph" },
  { id: "radar", label: "Radar" },
  { id: "sensors", label: "Sensor Data" },
  { id: "3d", label: "3D Attitude" },
];

export function RightTabs() {
  const [tab, setTab] = useState<Tab>("graph");
  const st = useTelemetry();
  const scene = useRef<DroneSceneHandle>(null);
  return (
    <section className="panel flex h-full min-h-0 flex-col">
      <header className="panel-head flex gap-1 overflow-x-auto px-2 py-1.5">
        {TABS.map((t) => <button key={t.id} className={`chip-tab shrink-0 ${tab === t.id ? "active" : ""}`} onClick={() => setTab(t.id)}>{t.label}</button>)}
      </header>
      <div className="min-h-0 flex-1 overflow-auto p-2.5">
        {tab === "graph" && <AgentGraph />}
        {tab === "radar" && <RadarView />}
        {tab === "sensors" && <SensorData />}
        {tab === "3d" && (
          <div className="relative h-full min-h-[240px]">
            {st?.imu.latest ? (
              <Suspense fallback={<Empty>Loading 3D view…</Empty>}>
                <DroneScene ref={scene} smoothing lockYaw={false} cameraTilt={st?.servo?.tilt_deg ?? null} />
              </Suspense>
            ) : <Empty>{st?.imu.status.error ?? "Waiting for IMU data…"}</Empty>}
            <button className="btn btn-sm absolute bottom-1 left-1" onClick={() => scene.current?.resetView()}>Reset view</button>
            <span className="absolute bottom-1 right-1 text-[9.5px] text-dim">estimated attitude · yaw relative (no magnetometer)</span>
          </div>
        )}
      </div>
    </section>
  );
}
