import { useCallback, useEffect, useState } from "react";
import { LoginScreen } from "./components/auth/LoginScreen";
import { ToastProvider } from "./components/common/toast";
import { TopBar, type View } from "./components/console/TopBar";
import { telemetry } from "./hooks/useTelemetry";
import { get, onUnauthorized } from "./services/api";
import { ConsolePage } from "./pages/ConsolePage";
import { CalibrationPage, RecordingsPage, SystemPage } from "./pages/SecondaryPages";

const VIEWS: View[] = ["console", "recordings", "system", "calibration"];
const readView = (): View => {
  const h = location.hash.replace("#/", "") as View;
  return VIEWS.includes(h) ? h : "console";
};

function Shell() {
  const [view, setViewState] = useState<View>(readView);
  const setView = (v: View) => {
    location.hash = `#/${v}`;
    setViewState(v);
  };
  useEffect(() => {
    const f = () => setViewState(readView());
    window.addEventListener("hashchange", f);
    return () => window.removeEventListener("hashchange", f);
  }, []);
  useEffect(() => {
    telemetry.start();
    return () => telemetry.stop();
  }, []);
  return (
    <div className="flex h-full flex-col">
      <TopBar view={view} setView={setView} />
      <main className="min-h-0 flex-1 overflow-y-auto lg:overflow-hidden">
        {view === "console" && <ConsolePage />}
        {view !== "console" && (
          <div className="h-full overflow-y-auto">
            {view === "recordings" && <RecordingsPage />}
            {view === "system" && <SystemPage />}
            {view === "calibration" && <CalibrationPage />}
          </div>
        )}
      </main>
    </div>
  );
}

export default function App() {
  const [auth, setAuth] = useState<"checking" | "ok" | "login">("checking");
  const check = useCallback(async () => {
    try {
      const s = await get<{ auth_enabled: boolean; authenticated: boolean }>("/api/auth/status");
      setAuth(!s.auth_enabled || s.authenticated ? "ok" : "login");
    } catch {
      setAuth("ok"); // backend unreachable: the shell shows the connection banner and retries
    }
  }, []);
  useEffect(() => {
    void check();
    return onUnauthorized(() => {
      telemetry.stop();
      setAuth("login");
    });
  }, [check]);
  return (
    <ToastProvider>
      {auth === "checking" ? null : auth === "login" ? <LoginScreen onLogin={() => setAuth("ok")} /> : <Shell />}
    </ToastProvider>
  );
}
