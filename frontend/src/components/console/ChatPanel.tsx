import { Bot, Check, Copy, Eye, Loader2, Minus, Plus, ScanSearch, Send, Square, Trash2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { telemetry, useTelemetry } from "../../hooks/useTelemetry";
import { del, errorMessage, get, post, streamNdjson } from "../../services/api";
import type { PlanStep } from "../../types/telemetry";
import { copyText, RichText } from "../common/RichText";
import { useToast } from "../common/toast";
import { RecordingsTab } from "./RecordingsTab";

const QUICK = ["What do you see?", "Where is the ball?", "Look up", "Follow the person", "How warm is it?"];

interface Turn {
  id: string;
  role: "user" | "agent";
  text: string;
  steps?: PlanStep[];
  status?: "running" | "done" | "stopped" | "error";
  error?: string;
  auto?: boolean;
  vision?: boolean;
}

interface HistoryRow { id: number; ts: number; role: string; content: string; provider: string | null; meta: Record<string, unknown> }

function StepIcon({ s }: { s: PlanStep["status"] }) {
  if (s === "running") return <Loader2 size={11} className="animate-spin text-accent" />;
  if (s === "done") return <Check size={11} className="text-accent" />;
  if (s === "failed") return <X size={11} className="text-crit" />;
  if (s === "skipped") return <Minus size={11} className="text-warn" />;
  return <span className="inline-block h-2 w-2 rounded-full border border-dim" />;
}

/** What YOLO looks for — edited right where you talk to Qwen. */
function YoloTargets() {
  const st = useTelemetry();
  const toast = useToast();
  const [classes, setClasses] = useState<string[]>([]);
  const [input, setInput] = useState("");
  const [adding, setAdding] = useState(false);
  const prompts = st?.detector?.prompts ?? st?.detections?.prompts ?? [];
  const found = st?.detections?.detections.length ?? 0;

  useEffect(() => {
    get<{ classes: string[] }>("/api/detector/classes").then((r) => setClasses(r.classes)).catch(() => {});
  }, [st?.detector?.engine]);

  const save = async (next: string[]) => {
    try {
      await post("/api/detector/prompts", { prompts: next });
    } catch (e) {
      toast(errorMessage(e), "error");
    }
  };
  const add = async () => {
    const items = input.split(",").map((s) => s.trim()).filter(Boolean);
    if (items.length) await save([...prompts, ...items]);
    setInput("");
    setAdding(false);
  };

  return (
    <div className="flex flex-wrap items-center gap-1 border-b border-line px-2.5 py-1.5 text-[11px]">
      <span className="mr-0.5 text-[10px] tracking-[0.12em] text-muted">YOLO LOOKS FOR</span>
      {prompts.map((p) => (
        <span key={p} className="inline-flex items-center gap-1 rounded-[3px] border border-line-2 bg-[#1a201c] px-1.5 py-[1px]">
          {p}
          <button aria-label={`stop looking for ${p}`} className="text-muted hover:text-crit" onClick={() => void save(prompts.filter((x) => x !== p))}><X size={10} /></button>
        </span>
      ))}
      {adding ? (
        <form className="flex" onSubmit={(e) => { e.preventDefault(); void add(); }}>
          <input autoFocus className="input !py-0 w-28 text-[11px]" list="strix-classes" placeholder="e.g. dog" value={input}
            onChange={(e) => setInput(e.target.value)} onBlur={() => !input.trim() && setAdding(false)} aria-label="Object for YOLO to look for" />
          <datalist id="strix-classes">{classes.map((c) => <option key={c} value={c} />)}</datalist>
        </form>
      ) : (
        <button className="rounded-[3px] border border-dashed border-line-2 px-1.5 py-[1px] text-muted hover:text-text" onClick={() => setAdding(true)} title="Add an object">
          <Plus size={10} className="inline" />
        </button>
      )}
      <span className="ml-auto text-[10px] text-accent">{found} in view</span>
    </div>
  );
}

function Chat() {
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
        : { id: `h${r.id}`, role: "agent" as const, text: r.content, steps: (r.meta.plan as PlanStep[]) ?? [],
            status: "done" as const, auto: !!r.meta.auto, vision: !!r.meta.vision }));
    }).catch(() => {});
    const onReset = () => setTurns([]);
    window.addEventListener("strix:reset", onReset);
    // Qwen speaks up by itself (new objects, a ball going under a cup, …) over the WebSocket.
    const off = telemetry.onChat((m) => setTurns((ts) => [...ts, { id: `w${m.id}`, role: "agent", text: m.text, auto: true, vision: m.vision, status: "done" }]));
    return () => {
      window.removeEventListener("strix:reset", onReset);
      off();
    };
  }, []);

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight });
  }, [turns]);

  const update = (id: string, fn: (t: Turn) => Turn) => setTurns((ts) => ts.map((t) => (t.id === id ? fn(t) : t)));

  const run = async (task: string) => {
    const t = task.trim();
    if (!t || busy) return;
    const id = `a${Date.now()}`;
    setTurns((ts) => [...ts, { id: `u${Date.now()}`, role: "user", text: t }, { id, role: "agent", text: "", steps: [], status: "running" }]);
    setInput("");
    setBusy(true);
    const ac = new AbortController();
    abort.current = ac;
    try {
      for await (const ev of streamNdjson<Record<string, unknown>>("/api/agent/run", { task: t }, ac.signal)) {
        switch (ev.type) {
          case "plan":
            update(id, (x) => ({ ...x, steps: ev.steps as PlanStep[] }));
            break;
          case "step":
            update(id, (x) => ({ ...x, steps: (x.steps ?? []).map((s, i) => (i === ev.index ? { ...s, status: ev.status as PlanStep["status"] } : s)) }));
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
          case "vision":
            update(id, (x) => ({ ...x, vision: !!ev.image_sent }));
            break;
          case "done":
            update(id, (x) => ({ ...x, status: "done" }));
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

  useEffect(() => {
    const onAsk = (e: Event) => void run((e as CustomEvent<string>).detail);
    window.addEventListener("strix:ask", onAsk);
    return () => window.removeEventListener("strix:ask", onAsk);
  });

  return (
    <>
      <div ref={scroller} className="min-h-0 flex-1 space-y-2 overflow-y-auto p-2.5 text-[12.5px]">
        {turns.length === 0 && (
          <div className="flex flex-wrap gap-1">
            {QUICK.map((q) => <button key={q} className="btn btn-sm !normal-case !tracking-normal" onClick={() => void run(q)}>{q}</button>)}
          </div>
        )}
        {turns.map((t) => t.role === "user" ? (
          <div key={t.id} className="ml-10 rounded-[3px] border border-line-2 bg-[#171c18] px-2 py-1.5">{t.text}</div>
        ) : (
          <div key={t.id} className="group mr-4 rounded-[3px] border border-line bg-[#0d100e] px-2 py-1.5">
            <div className="mb-0.5 flex items-center gap-1.5 text-[10px] tracking-wider text-accent">
              {t.auto ? <Eye size={11} /> : <Bot size={11} />}
              <span>QWEN</span>
              {t.vision && <span title="Qwen looked at the camera image" className="text-synth"><ScanSearch size={11} /></span>}
              {t.status === "running" && <Loader2 size={11} className="animate-spin" />}
              {t.status === "done" && t.text && (
                <button className="ml-auto text-muted opacity-0 hover:text-text group-hover:opacity-100" title="Copy"
                  onClick={() => void copyText(t.text).then((ok) => toast(ok ? "Copied" : "Copy failed", ok ? "ok" : "error"))}>
                  <Copy size={11} />
                </button>
              )}
            </div>
            {t.status === "running" && (t.steps ?? []).length > 0 && (
              <ol className="mb-1 space-y-0.5 text-[11px] text-muted">
                {t.steps!.map((s, i) => (
                  <li key={i} className="flex items-center gap-1.5"><StepIcon s={s.status} />{s.text}</li>
                ))}
              </ol>
            )}
            {t.text ? <RichText text={t.text} /> : t.status === "running" && <span className="text-dim">thinking…</span>}
            {t.error && <div className="mt-1 text-[11px] text-crit">{t.error}</div>}
          </div>
        ))}
      </div>
      <div className="border-t border-line p-2">
        <form className="flex gap-1" onSubmit={(e) => { e.preventDefault(); void run(input); }}>
          <input className="input min-w-0 flex-1" maxLength={1000} placeholder={busy ? "Qwen is working… (Esc to stop)" : "Ask Qwen… e.g. where is the ball?"}
            value={input} onChange={(e) => setInput(e.target.value)} disabled={busy} aria-label="Message for Qwen" />
          {busy ? (
            <button type="button" className="btn btn-danger" onClick={() => { void post("/api/agent/stop").catch(() => {}); abort.current?.abort(); }} title="Stop"><Square size={12} /></button>
          ) : (
            <button type="submit" className="btn btn-primary" disabled={!input.trim()} title="Send"><Send size={13} /></button>
          )}
        </form>
      </div>
    </>
  );
}

type Tab = "qwen" | "recordings";

/** Qwen + YOLO in one place, with recordings as the second tab. */
export function ChatPanel() {
  const st = useTelemetry();
  const q = st?.ai?.qwen;
  const [tab, setTab] = useState<Tab>("qwen");
  const ready = q?.state === "ready";
  const clear = async () => {
    await del("/api/agent/history").catch(() => {});
    window.dispatchEvent(new Event("strix:reset"));
  };
  return (
    <section className="panel flex h-full min-h-0 flex-col">
      <header className="panel-head flex items-center gap-1 px-2 py-1.5">
        <button className={`chip-tab ${tab === "qwen" ? "active" : ""}`} onClick={() => setTab("qwen")}>
          <span className={`mr-1 inline-block h-1.5 w-1.5 rounded-full ${ready ? "bg-accent" : "bg-warn"}`} />Qwen
        </button>
        <button className={`chip-tab ${tab === "recordings" ? "active" : ""}`} onClick={() => setTab("recordings")}>Recordings</button>
        {tab === "qwen" && (
          <div className="ml-auto flex items-center gap-2">
            <button className="rounded-[3px] border border-blue/50 bg-blue/10 px-1.5 text-[10px] tracking-wider text-blue"
              title="Ask Qwen what the camera sees" onClick={() => window.dispatchEvent(new CustomEvent("strix:ask", { detail: "What do you see?" }))}>
              <ScanSearch size={10} className="mr-0.5 inline" />IDENTIFY
            </button>
            <button className="text-muted hover:text-crit" title="Clear chat" onClick={() => void clear()}><Trash2 size={12} /></button>
          </div>
        )}
      </header>
      {/* the chat stays mounted so a running task keeps streaming while Recordings is open */}
      <div className={tab === "qwen" ? "flex min-h-0 flex-1 flex-col" : "hidden"}>
        <YoloTargets />
        <Chat />
      </div>
      {tab === "recordings" && <div className="min-h-0 flex-1 overflow-auto p-2.5"><RecordingsTab /></div>}
    </section>
  );
}
