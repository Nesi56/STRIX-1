import { Bot } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { AIAssistant, type DockPosition } from "./components/ai/AIAssistant";
import { LoginScreen } from "./components/auth/LoginScreen";
import { ToastProvider } from "./components/common/toast";
import { Header, PAGES, StatusBanners, type Page } from "./components/layout/Header";
import { telemetry } from "./hooks/useTelemetry";
import { get, onUnauthorized } from "./services/api";
import { DashboardPage, EnvironmentPage, RecordingsPage, SettingsPage, SystemPage, TelemetryPage } from "./pages/Pages";

function readHash(): Page {
  const h = location.hash.replace("#/", "") as Page;
  return PAGES.some((p) => p.id === h) ? h : "dashboard";
}

function readDock(): DockPosition {
  try {
    const v = localStorage.getItem("strix.dock") as DockPosition | null;
    if (v) return v;
  } catch { /* ignore */ }
  return window.innerWidth >= 1280 ? "right" : "hidden";
}

function Shell() {
  const [page, setPageState] = useState<Page>(readHash);
  const [dock, setDockState] = useState<DockPosition>(readDock);
  const setPage = (p: Page) => {
    location.hash = `#/${p}`;
    setPageState(p);
  };
  const setDock = (d: DockPosition) => {
    setDockState(d);
    try { localStorage.setItem("strix.dock", d); } catch { /* ignore */ }
  };
  useEffect(() => {
    const f = () => setPageState(readHash());
    window.addEventListener("hashchange", f);
    return () => window.removeEventListener("hashchange", f);
  }, []);
  useEffect(() => {
    telemetry.start();
    return () => telemetry.stop();
  }, []);

  const content = {
    dashboard: <DashboardPage />, telemetry: <TelemetryPage />, environment: <EnvironmentPage />,
    recordings: <RecordingsPage />, system: <SystemPage />, settings: <SettingsPage />,
  }[page];

  return (
    <div className="flex h-full flex-col">
      <Header page={page} setPage={setPage} onToggleAI={() => setDock(dock === "hidden" ? (window.innerWidth >= 1280 ? "right" : "bottom") : "hidden")} />
      <StatusBanners />
      <div className={`flex min-h-0 flex-1 ${dock === "bottom" ? "flex-col" : "flex-row"}`}>
        <main className="min-h-0 min-w-0 flex-1 overflow-y-auto p-3">{content}</main>
        {dock === "right" && (
          <aside className="hidden w-[380px] shrink-0 border-l border-line p-2 xl:block 2xl:w-[420px]">
            <AIAssistant dock={dock} setDock={setDock} />
          </aside>
        )}
        {dock === "right" && (
          <aside className="fixed inset-x-0 bottom-0 z-40 h-[60vh] border-t border-line bg-bg p-2 xl:hidden">
            <AIAssistant dock="bottom" setDock={setDock} />
          </aside>
        )}
        {dock === "bottom" && (
          <aside className="h-[45vh] shrink-0 border-t border-line p-2 max-xl:fixed max-xl:inset-x-0 max-xl:bottom-0 max-xl:z-40 max-xl:h-[60vh] max-xl:bg-bg">
            <AIAssistant dock={dock} setDock={setDock} />
          </aside>
        )}
      </div>
      {dock === "hidden" && (
        <button className="btn btn-primary fixed bottom-4 right-4 z-40 !rounded-full !px-3 !py-2 shadow-lg" onClick={() => setDock(window.innerWidth >= 1280 ? "right" : "bottom")} title="Open AI assistant">
          <Bot size={16} /> <span className="hidden sm:inline">Assistant</span>
        </button>
      )}
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
      setAuth("ok"); // backend unreachable: show the shell; it will display the connection banner and retry
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
