import { EnvironmentPanel } from "../components/environment/EnvironmentPanel";
import { RecordingsPanel } from "../components/recordings/RecordingsPanel";
import { SettingsPanel } from "../components/settings/SettingsPanel";
import { SystemPanel } from "../components/system/SystemPanel";

export const RecordingsPage = () => <div className="p-2"><RecordingsPanel /></div>;

export const SystemPage = () => (
  <div className="grid gap-2 p-2 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
    <SystemPanel detailed />
    <EnvironmentPanel />
  </div>
);

export const CalibrationPage = () => <div className="p-2"><SettingsPanel /></div>;
