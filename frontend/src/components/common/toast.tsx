import { createContext, useCallback, useContext, useState, type ReactNode } from "react";

type Tone = "info" | "ok" | "warn" | "error";
interface Toast { id: number; tone: Tone; text: string }

const Ctx = createContext<(text: string, tone?: Tone) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((text: string, tone: Tone = "info") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t.slice(-3), { id, tone, text }]);
    window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), tone === "error" ? 7000 : 3500);
  }, []);
  const tone: Record<Tone, string> = {
    info: "border-blue/40 text-[#cfe0ff]",
    ok: "border-ok/40 text-ok",
    warn: "border-warn/40 text-warn",
    error: "border-crit/50 text-crit",
  };
  return (
    <Ctx.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed bottom-4 left-1/2 z-[100] flex w-[min(92vw,420px)] -translate-x-1/2 flex-col gap-2">
        {toasts.map((t) => (
          <div key={t.id} role="status" className={`panel pointer-events-auto px-3 py-2 text-[12px] ${tone[t.tone]}`}>
            {t.text}
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}

export const useToast = () => useContext(Ctx);
