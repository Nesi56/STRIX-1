import { useCallback, useEffect, useState } from "react";
import { LoginScreen } from "./components/auth/LoginScreen";
import { ToastProvider } from "./components/common/toast";
import { ConnectionBanner, TopBar } from "./components/console/TopBar";
import { HubNav } from "./components/console/HubNav";
import { telemetry, useTelemetrySnapshot } from "./hooks/useTelemetry";
import { usePoll } from "./hooks/usePoll";
import { get, onUnauthorized } from "./services/api";
import { droneBase, parseRoute, setDrone } from "./services/drone";
import { ConsolePage } from "./pages/ConsolePage";
import { FleetPage } from "./pages/FleetPage";
import type { FleetResponse } from "./types/fleet";

function Shell({ nav }: { nav?: React.ReactNode }) {
  const snap = useTelemetrySnapshot();
  useEffect(() => {
    telemetry.start();
    return () => telemetry.stop();
  }, []);
  return (
    <div className="flex h-full flex-col">
      <TopBar nav={nav} />
      <ConnectionBanner conn={snap.conn} attempt={snap.reconnectAttempt} />
      <main className="min-h-0 flex-1 overflow-y-auto lg:overflow-hidden">
        <ConsolePage />
      </main>
    </div>
  );
}

/** One drone's console inside the hub. Mounted fresh (key = id) for every drone. */
function DroneConsole({ id, fleet }: { id: string; fleet: FleetResponse | null }) {
  // Point every request at this drone before any child mounts.
  useState(() => {
    setDrone(id);
    telemetry.reset();
    return id;
  });
  useEffect(() => () => {
    // When switching drones the next console has already claimed the routes; leave them alone.
    if (droneBase() === `/d/${id}`) {
      telemetry.reset();
      setDrone(null);
    }
  }, [id]);
  return <Shell nav={<HubNav current={id} fleet={fleet} />} />;
}

function HubApp() {
  const [route, setRoute] = useState(() => parseRoute(location.hash));
  useEffect(() => {
    const onHash = () => setRoute(parseRoute(location.hash));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  // Faster while the fleet is on screen; the console has its own live telemetry.
  const { data, error } = usePoll<FleetResponse>("/api/hub/fleet", route.view === "fleet" ? 1000 : 3000);
  if (route.view === "drone") return <DroneConsole key={route.id} id={route.id} fleet={data} />;
  return <FleetPage fleet={data} error={error} />;
}

export default function App() {
  const [auth, setAuth] = useState<"checking" | "ok" | "login">("checking");
  const [hub, setHub] = useState(false);
  const check = useCallback(async () => {
    // A hub answers /api/hub/info; a drone's own service answers 404.
    const isHub = await get<{ hub?: boolean }>("/api/hub/info").then((r) => r.hub === true, () => false);
    setHub(isHub);
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
      {auth === "checking" ? null
        : auth === "login" ? <LoginScreen hub={hub} onLogin={() => setAuth("ok")} />
          : hub ? <HubApp /> : <Shell />}
    </ToastProvider>
  );
}
