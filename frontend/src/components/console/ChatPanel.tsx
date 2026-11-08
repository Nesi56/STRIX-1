import { Bot, Check, Copy, Loader2, Minus, Send, Square, Trash2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTelemetry } from "../../hooks/useTelemetry";
import { del, errorMessage, get, post, streamNdjson } from "../../services/api";
import type { AIStats, PlanStep } from "../../types/telemetry";
import { copyText, RichText } from "../common/RichText";
import { useToast } from "../common/toast";
import { ConsolePanel } from "./ConsolePanel";

const QUICK = [
  "Find the person and keep them centered",
  "How far away is the person and how should I move?",
  "Scan the area and tell me what you see",
  "Look up 15 degrees",
  "Is the IMU behaving normally?",
  "What are the temperature and humidity?",
];

interface Turn {
  id: string;
  role: "user" | "agent";
  text: string;
  planner?: string | null;
  thought?: string;
  steps?: PlanStep[];
  progress?: string[];
  suggestions?: string[];
  status?: "running" | "done" | "stopped" | "error";
  error?: string;
  stats?: AIStats;
  reporter?: string;
  totalMs?: number;
}

interface HistoryRow { id: number; ts: number; role: string; content: string; provider: string | null; meta: Record<string, unknown> }

function StepIcon({ s }: { s: PlanStep["status"] }) {
  if (s === "running") return <Loader2 size={11} className="animate-spin text-accent" />;
  if (s === "done") return <Check size={11} className="text-accent" />;
  if (s === "failed") return <X size={11} className="text-crit" />;
  if (s === "skipped") return <Minus size={11} className="text-warn" />;
  return <span className="inline-block h-2 w-2 rounded-full border border-dim" />;
}

export function ChatPanel() {
  const st = useTelemetry();
  const qwen = st?.ai?.qwen;
  const toast = useToast();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);
  const abort = useRef<AbortController | null>(null);

  useEffect(() => {
    get<HistoryRow[]>("/api/agent/history?limit=40").then((rows) => {
      setTurns(rows.map((r) => r.role === "user"
        ? { id: `h${r.id}`, role: "user" as const, text: r.content }
        : { id: `h${r.id}`, role: "agent" as const, text: r.content, planner: (r.meta.planner as string) ?? null,
            steps: (r.meta.plan as PlanStep[]) ?? [], suggestions: (r.meta.suggestions as string[]) ?? [],
            thought: r.meta.thought as string, status: "done" as const, reporter: r.provider ?? undefined,
            stats: r.meta.stats as AIStats }));
    }).catch(() => {});
    const onReset = () => setTurns([]);
    window.addEventListener("strix:reset", onReset);
    return () => window.removeEventListener("strix:reset", onReset);
  }, []);

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight });
  }, [turns]);

  const update = (id: string, fn: (t: Turn) => Turn) => setTurns((ts) => ts.map((t) => (t.id === id ? fn(t) : t)));

  const run = async (task: string) => {
    const t = task.trim();
    if (!t || busy) return;
    const id = `a${Date.now()}`;
    setTurns((ts) => [...ts, { id: `u${Date.now()}`, role: "user", text: t },
      { id, role: "agent", text: "", steps: [], progress: [], suggestions: [], status: "running" }]);
    setInput("");
    setBusy(true);
    const ac = new AbortController();
    abort.current = ac;
    try {
      for await (const ev of streamNdjson<Record<string, unknown>>("/api/agent/run", { task: t }, ac.signal)) {
        switch (ev.type) {
          case "plan":
            update(id, (x) => ({ ...x, planner: ev.planner as string, thought: ev.thought as string, steps: ev.steps as PlanStep[] }));
            break;
          case "step":
            update(id, (x) => ({ ...x, steps: (x.steps ?? []).map((s, i) => (i === ev.index ? { ...s, status: ev.status as PlanStep["status"], result: ev.status === "running" ? undefined : (ev.text as string) } : s)) }));
            break;
          case "progress":
            update(id, (x) => ({ ...x, progress: [...(x.progress ?? []), ev.text as string].slice(-6) }));
            break;
          case "suggestions":
            update(id, (x) => ({ ...x, suggestions: ev.items as string[] }));
            break;
          case "delta":
            update(id, (x) => ({ ...x, text: x.text + (ev.text as string) }));
            break;
          case "replace":
            update(id, (x) => ({ ...x, text: ev.text as string }));
            break;
          case "error":
            update(id, (x) => ({ ...x, error: ev.message as string }));
            break;
          case "stopped":
            update(id, (x) => ({ ...x, status: "stopped", error: `stopped: ${ev.reason as string}` }));
            break;
          case "done":
            update(id, (x) => ({ ...x, status: "done", stats: ev.stats as AIStats, reporter: ev.reporter as string, totalMs: ev.total_ms as number }));
            break;
        }
      }
    } catch (e) {
      update(id, (x) => ({ ...x, status: "error", error: ac.signal.aborted ? "stopped" : errorMessage(e) }));
    } finally {
      update(id, (x) => (x.status === "running" ? { ...x, status: "done" } : x));
      setBusy(false);
      abort.current = null;
    }
  };

  const stop = async () => {
    await post("/api/agent/stop").catch(() => {});
  };

  const clear = async () => {
    await del("/api/agent/history").catch(() => {});
    setTurns([]);
  };

  return (
    <ConsolePanel
      title="Chat"
      className="h-full"
      bodyClassName="!p-0 flex flex-col"
      actions={
        <>
          <span className="rounded-[3px] border border-blue/50 bg-blue/10 px-1.5 text-[10px] tracking-wider text-blue">TEXT</span>
          <span className={`rounded-[3px] border px-1.5 text-[10px] tracking-wider ${qwen?.state === "ready" ? "border-accent-dim bg-[#173a27] text-accent" : "border-warn/50 text-warn"}`}
            title={qwen?.detail ?? undefined}>
            QWEN {qwen?.state === "ready" ? (qwen.last?.tokens_per_s ? `${qwen.last.tokens_per_s.toFixed(1)} t/s` : "READY") : (qwen?.state ?? "…").toUpperCase()}
          </span>
          <button className="text-muted hover:text-crit" title="Clear chat" onClick={() => void clear()}><Trash2 size={12} /></button>
        </>
      }
    >
      <div ref={scroller} className="min-h-0 flex-1 space-y-2 overflow-y-auto p-2.5 text-[12px]">
        {turns.length === 0 && (
          <div className="space-y-2 text-muted">
            <p>Give Qwen a task. It plans camera moves, drives the tilt servo, reads the MPU6050, DHT11 and YOLO detections,
              estimates distances and suggests how to move.{qwen?.state !== "ready" && <span className="text-warn"> Qwen is {qwen?.state ?? "offline"} — a keyword planner and rule-based report stand in.</span>}</p>
            <div className="flex flex-wrap gap-1">
              {QUICK.map((q) => <button key={q} className="btn btn-sm !normal-case !tracking-normal" onClick={() => void run(q)}>{q}</button>)}
            </div>
          </div>
        )}
        {turns.map((t) => t.role === "user" ? (
          <div key={t.id} className="ml-8 rounded-[3px] border border-line-2 bg-[#171c18] px-2 py-1.5">{t.text}</div>
        ) : (
          <div key={t.id} className="mr-2 rounded-[3px] border border-line bg-[#0d100e] px-2 py-1.5">
            <div className="mb-1 flex items-center gap-1.5 text-[10px] tracking-wider">
              <Bot size={12} className="text-accent" />
              <span className="text-accent">QWEN AGENT</span>
              {t.planner && <span className="rounded-[3px] border border-line-2 px-1 text-muted">{t.planner === "qwen" ? "QWEN PLAN" : "RULE PLAN"}</span>}
              {t.status === "running" && <Loader2 size={11} className="animate-spin text-accent" />}
              {t.status === "done" && t.text && (
                <button className="ml-auto text-muted hover:text-text" title="Copy" onClick={() => void copyText(t.text).then((ok) => toast(ok ? "Copied" : "Copy failed", ok ? "ok" : "error"))}>
                  <Copy size={11} />
                </button>
              )}
            </div>
            {t.thought && <div className="mb-1 text-[11px] italic text-dim">{t.thought}</div>}
            {(t.steps ?? []).length > 0 && (
              <ol className="mb-1 space-y-0.5 text-[11px]">
                {t.steps!.map((s, i) => (
                  <li key={i} className="flex items-start gap-1.5">
                    <span className="mt-[3px]"><StepIcon s={s.status} /></span>
                    <span>{s.text}{s.result && <span className="block text-[10.5px] text-dim">{s.result}</span>}</span>
                  </li>
                ))}
              </ol>
            )}
            {t.status === "running" && (t.progress ?? []).length > 0 && <div className="mb-1 text-[10.5px] text-dim">{t.progress![t.progress!.length - 1]}</div>}
            {t.text ? <RichText text={t.text} /> : t.status === "running" && <span className="text-dim">working…</span>}
            {t.suggestions && t.suggestions.length > 0 && t.reporter === "qwen" && (
              <details className="mt-1 text-[10.5px] text-muted"><summary className="cursor-pointer">computed suggestions</summary>
                <ul className="ml-4 list-disc">{t.suggestions.map((s, i) => <li key={i}>{s}</li>)}</ul></details>
            )}
            {t.error && <div className="mt-1 text-[11px] text-crit">⚠ {t.error}</div>}
            {t.status !== "running" && (t.totalMs != null || t.stats?.latency_ms != null) && (
              <div className="mt-1 text-[10px] text-dim">
                {t.totalMs != null && `${(t.totalMs / 1000).toFixed(1)} s total`}
                {t.stats?.tokens_per_s != null && ` · ${t.stats.tokens_per_s.toFixed(1)} tok/s`}
                {t.reporter && ` · report: ${t.reporter === "qwen" ? "Qwen" : "rule-based"}`}
              </div>
            )}
          </div>
        ))}
      </div>
      <div className="border-t border-line p-2">
        <form className="flex gap-1" onSubmit={(e) => { e.preventDefault(); void run(input); }}>
          <input className="input min-w-0 flex-1" maxLength={1000} placeholder={busy ? "Qwen is working… (Esc to stop)" : "give Qwen a task, e.g. find the person and keep them centered"}
            value={input} onChange={(e) => setInput(e.target.value)} disabled={busy} aria-label="Task for Qwen" />
          {busy ? (
            <button type="button" className="btn btn-danger" onClick={() => { void stop(); abort.current?.abort(); }} title="Stop the agent"><Square size={12} /></button>
          ) : (
            <button type="submit" className="btn btn-primary" disabled={!input.trim()} title="Send"><Send size={13} /></button>
          )}
        </form>
        <div className="mt-1 text-[10px] text-dim">Qwen runs locally on the Pi · it can move only the camera tilt servo (when enabled) · movement advice is for the pilot.</div>
      </div>
    </ConsolePanel>
  );
}
