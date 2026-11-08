import { useCallback, useEffect, useState } from "react";
import { LoginScreen } from "./components/auth/LoginScreen";
import { ToastProvider } from "./components/common/toast";
import { ConnectionBanner, TopBar } from "./components/console/TopBar";
import { telemetry, useTelemetrySnapshot } from "./hooks/useTelemetry";
import { get, onUnauthorized } from "./services/api";
import { ConsolePage } from "./pages/ConsolePage";

function Shell() {
  const snap = useTelemetrySnapshot();
  useEffect(() => {
    telemetry.start();
    return () => telemetry.stop();
  }, []);
  return (
    <div className="flex h-full flex-col">
      <TopBar />
      <ConnectionBanner conn={snap.conn} attempt={snap.reconnectAttempt} />
      <main className="min-h-0 flex-1 overflow-y-auto lg:overflow-hidden">
        <ConsolePage />
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
