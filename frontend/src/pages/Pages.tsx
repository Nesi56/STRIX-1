import { CameraPanel } from "../components/camera/CameraPanel";
import { EnvironmentPanel } from "../components/environment/EnvironmentPanel";
import { MapPanel } from "../components/map/MapPanel";
import { OrientationPanel } from "../components/orientation/OrientationPanel";
import { RecordingsPanel, SessionControl } from "../components/recordings/RecordingsPanel";
import { ServoPanel } from "../components/servo/ServoPanel";
import { SettingsPanel } from "../components/settings/SettingsPanel";
import { SystemPanel } from "../components/system/SystemPanel";
import { OrientationChart, TelemetryPanel } from "../components/telemetry/TelemetryPanel";
import { Panel } from "../components/common/ui";
import { Database, Rotate3d } from "lucide-react";

export function DashboardPage() {
  return (
    <div className="grid gap-3 xl:grid-cols-12">
      <CameraPanel className="min-h-[340px] xl:col-span-7 xl:row-span-2" />
      <OrientationPanel className="min-h-[340px] xl:col-span-5 xl:row-span-2" />
      <TelemetryPanel className="xl:col-span-7" chartHeight={130} />
      <div className="grid gap-3 xl:col-span-5">
        <EnvironmentPanel showChart={false} />
        <ServoPanel />
      </div>
      <SystemPanel className="xl:col-span-7" />
      <Panel title="Recording" icon={<Database size={14} />} className="xl:col-span-5"><SessionControl /></Panel>
      <MapPanel className="xl:col-span-12" />
    </div>
  );
}

export function TelemetryPage() {
  return (
    <div className="grid gap-3 xl:grid-cols-2">
      <TelemetryPanel className="xl:col-span-2" windowS={60} chartHeight={190} />
      <OrientationPanel className="min-h-[380px]" />
      <Panel title="Estimated Orientation History" icon={<Rotate3d size={14} />}>
        <OrientationChart windowS={60} />
        <p className="mt-2 text-[11px] text-muted">Roll/pitch from the complementary filter; relative yaw integrates gyro Z and drifts (no magnetometer).</p>
      </Panel>
    </div>
  );
}

export function EnvironmentPage() {
  return (
    <div className="grid gap-3 xl:grid-cols-3">
      <EnvironmentPanel className="xl:col-span-1" />
      <CameraPanel className="min-h-[320px] xl:col-span-2" />
      <MapPanel className="xl:col-span-3" defaultMode="B" />
    </div>
  );
}

export function RecordingsPage() {
  return <RecordingsPanel />;
}

export function SystemPage() {
  return (
    <div className="grid gap-3 xl:grid-cols-2">
      <SystemPanel className="xl:col-span-2" detailed />
      <ServoPanel />
      <EnvironmentPanel showChart={false} />
    </div>
  );
}

export function SettingsPage() {
  return <SettingsPanel />;
}
