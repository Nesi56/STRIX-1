import { Bot, Camera, Check, Cloud, Copy, Cpu, PanelBottom, PanelRight, Send, ShieldAlert, Sparkles, Square, Trash2, X } from "lucide-react";
import { Fragment, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useTelemetry } from "../../hooks/useTelemetry";
import { del, errorMessage, get, post, streamNdjson } from "../../services/api";
import type { AIStats, ChatMessage } from "../../types/telemetry";
import { StateBadge, Tag, Toggle } from "../common/ui";
import { useToast } from "../common/toast";

type Mode = "local" | "grok" | "hybrid";
export type DockPosition = "right" | "bottom" | "hidden";

const QUICK = [
  "What is the current pitch and roll?",
  "Summarize the last 60 seconds of sensor readings.",
  "Has the temperature changed?",
  "What does the camera currently show?",
  "Is the IMU behaving abnormally?",
  "Summarize the latest recorded observations.",
];

const TAG_TONE: Record<string, "ok" | "warn" | "muted" | "cyan" | "blue" | "synth" | "crit"> = {
  MEASURED: "ok", "MEASURED→DERIVED": "ok", DERIVED: "blue", INTERPRETATION: "warn", UNAVAILABLE: "muted", DETECTOR: "cyan", HEALTH: "synth",
};
const INLINE = /(\*\*[^*]+\*\*|`[^`]+`|\[(?:MEASURED→DERIVED|MEASURED|INTERPRETATION|UNAVAILABLE|DERIVED|DETECTOR|HEALTH)\])/g;

function inline(text: string): ReactNode[] {
  return text.split(INLINE).filter(Boolean).map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={i}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("`") && part.endsWith("`")) return <code key={i} className="num rounded bg-white/5 px-1">{part.slice(1, -1)}</code>;
    const m = part.match(/^\[(.+)\]$/);
    if (m && TAG_TONE[m[1]]) return <Tag key={i} tone={TAG_TONE[m[1]]}>{m[1]}</Tag>;
    return <Fragment key={i}>{part}</Fragment>;
  });
}

/** Safe minimal rich-text: paragraphs, bullet lists, bold, code, claim tags. No HTML injection. */
export function RichText({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  let list: string[] = [];
  const flush = () => {
    if (list.length) {
      blocks.push(<ul key={blocks.length} className="ml-4 list-disc space-y-0.5">{list.map((l, i) => <li key={i}>{inline(l)}</li>)}</ul>);
      list = [];
    }
  };
  for (const line of text.split("\n")) {
    const b = line.match(/^\s*(?:[-*•]|\d+\.)\s+(.*)$/);
    if (b) {
      list.push(b[1]);
      continue;
    }
    flush();
    if (line.trim()) blocks.push(<p key={blocks.length}>{inline(line)}</p>);
  }
  flush();
  return <div className="space-y-1.5 leading-relaxed">{blocks}</div>;
}

async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through */
  }
  // Plain-HTTP LAN pages have no async clipboard API: fall back to execCommand.
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  const ok = document.execCommand("copy");
  document.body.removeChild(ta);
  return ok;
}

function StatsLine({ s }: { s?: AIStats }) {
  if (!s) return null;
  const parts = [
    s.latency_ms != null && `${(s.latency_ms / 1000).toFixed(1)} s`,
    s.ttft_ms != null && `TTFT ${(s.ttft_ms / 1000).toFixed(1)} s`,
    s.tokens_per_s != null && `${s.tokens_per_s.toFixed(1)} tok/s`,
    s.prompt_tokens != null && `${s.prompt_tokens}→${s.completion_tokens ?? "?"} tok${s.tokens_estimated ? " (est.)" : ""}`,
    s.cost_usd != null && s.cost_usd > 0 && `≈$${s.cost_usd.toFixed(4)}`,
  ].filter(Boolean);
  return <div className="num mt-1 text-[10px] text-dim">{parts.join(" · ")}</div>;
}

interface HistoryRow { id: number; ts: number; role: "user" | "assistant"; content: string; provider: string | null; model: string | null; meta: Record<string, unknown> }

export function AIAssistant({ dock, setDock }: { dock: DockPosition; setDock: (d: DockPosition) => void }) {
  const st = useTelemetry();
  const toast = useToast();
  const [mode, setMode] = useState<Mode>(() => {
    try { return (localStorage.getItem("strix.aiMode") as Mode) || "local"; } catch { return "local"; }
  });
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [snapshot, setSnapshot] = useState<string | null>(null);
  const [shareTelemetry, setShareTelemetry] = useState(true);
  const [consent, setConsent] = useState<{ text: string; deep: boolean } | null>(null);
  const abort = useRef<AbortController | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const qwen = st?.ai?.qwen;
  const grok = st?.ai?.grok;

  useEffect(() => {
    try { localStorage.setItem("strix.aiMode", mode); } catch { /* ignore */ }
  }, [mode]);

  useEffect(() => {
    get<HistoryRow[]>("/api/ai/history?limit=60").then((rows) => {
      setMessages(rows.map((r) => ({
        id: `h${r.id}`, role: r.role, content: r.content, ts: r.ts, provider: r.provider,
        label: (r.meta.label as string) ?? (r.provider === "strix-digest" ? "Telemetry digest (no AI model)" : undefined),
        local: r.meta.local as boolean | undefined, stats: r.meta.stats as AIStats | undefined, notes: r.meta.notes as string[] | undefined,
      })));
    }).catch(() => {});
  }, []);

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight });
  }, [messages]);

  const anomalies = useMemo(() => {
    const out: { tone: "warn" | "crit"; text: string }[] = [];
    for (const h of st?.imu_health ?? []) if (h.severity !== "info") out.push({ tone: h.severity === "critical" ? "crit" : "warn", text: `IMU: ${h.message}` });
    const env = st?.environment;
    if (env && ["disconnected", "error", "degraded"].includes(env.status.state)) out.push({ tone: "warn", text: `DHT11: ${env.status.error ?? env.status.state}` });
    for (const w of st?.system?.warnings ?? []) out.push({ tone: w.severity === "critical" ? "crit" : "warn", text: w.message });
    return out;
  }, [st?.imu_health, st?.environment, st?.system?.warnings]);

  const update = (id: string, fn: (m: ChatMessage) => ChatMessage) => setMessages((ms) => ms.map((m) => (m.id === id ? fn(m) : m)));

  const send = async (text: string, deep: boolean, externalConsent: boolean) => {
    const id = `a${Date.now()}`;
    setMessages((ms) => [...ms, { id: `u${Date.now()}`, role: "user", content: text, ts: Date.now() / 1000 },
      { id, role: "assistant", content: "", ts: Date.now() / 1000, streaming: true }]);
    setInput("");
    setBusy(true);
    const ac = new AbortController();
    abort.current = ac;
    try {
      for await (const ev of streamNdjson<Record<string, unknown>>("/api/ai/chat", {
        message: text, mode, deep_analysis: deep, external_consent: externalConsent,
        include_telemetry: shareTelemetry, snapshot_id: snapshot,
      }, ac.signal)) {
        switch (ev.type) {
          case "meta":
            update(id, (m) => ({ ...m, provider: ev.provider as string, label: ev.label as string, local: ev.local as boolean,
              notes: ev.notes as string[], telemetry_shared: ev.telemetry_shared as boolean, image_sent: ev.image_sent as boolean }));
            break;
          case "delta":
            update(id, (m) => ({ ...m, content: m.content + (ev.text as string) }));
            break;
          case "replace":
            update(id, (m) => ({ ...m, content: ev.text as string }));
            break;
          case "error":
            update(id, (m) => ({ ...m, error: ev.message as string, streaming: false }));
            break;
          case "fallback":
            setMessages((ms) => [...ms, { id: `f${Date.now()}`, role: "assistant", content: ev.text as string, ts: Date.now() / 1000,
              provider: "strix-digest", label: ev.label as string, local: true }]);
            break;
          case "done":
            update(id, (m) => ({ ...m, stats: ev.stats as AIStats, sanitized: ev.sanitized as boolean, streaming: false }));
            break;
        }
      }
      setSnapshot(null);
    } catch (e) {
      if (!ac.signal.aborted) update(id, (m) => ({ ...m, error: errorMessage(e), streaming: false }));
      else update(id, (m) => ({ ...m, error: "stopped", streaming: false }));
    } finally {
      update(id, (m) => ({ ...m, streaming: false }));
      setBusy(false);
      abort.current = null;
    }
  };

  const submit = (text: string, deep = false) => {
    const t = text.trim();
    if (!t || busy) return;
    const cloud = mode === "grok" || (mode === "hybrid" && deep);
    if (cloud) setConsent({ text: t, deep });
    else void send(t, deep, false);
  };

  const attachSnapshot = async () => {
    try {
      const s = await post<{ id: string }>("/api/camera/snapshot");
      setSnapshot(s.id);
    } catch (e) {
      toast(errorMessage(e), "error");
    }
  };

  const clear = async () => {
    if (!window.confirm("Clear the chat history?")) return;
    await del("/api/ai/history").catch(() => {});
    setMessages([]);
  };

  const targetVision = mode === "grok" ? grok?.vision_capable : qwen?.vision_capable;
  const containerCls = dock === "bottom" ? "h-full" : "h-full";

  return (
    <div className={`panel corner relative flex flex-col ${containerCls}`}>
      <header className="panel-head flex items-center gap-2 rounded-t-[10px] px-3 py-2">
        <Bot size={14} className="text-cyan" />
        <h2 className="label !text-[11px] !text-text">AI Assistant</h2>
        <div className="ml-auto flex items-center gap-1">
          <button className={`btn btn-sm btn-ghost ${dock === "right" ? "text-cyan" : ""}`} title="Dock right" onClick={() => setDock("right")}><PanelRight size={13} /></button>
          <button className={`btn btn-sm btn-ghost ${dock === "bottom" ? "text-cyan" : ""}`} title="Dock bottom" onClick={() => setDock("bottom")}><PanelBottom size={13} /></button>
          <button className="btn btn-sm btn-ghost" title="Hide" onClick={() => setDock("hidden")}><X size={13} /></button>
        </div>
      </header>
      <div className="space-y-2 border-b border-line px-3 py-2">
        <div className="flex gap-1">
          {(["local", "grok", "hybrid"] as Mode[]).map((m) => (
            <button key={m} className={`btn btn-sm flex-1 justify-center ${mode === m ? "btn-primary" : ""}`} onClick={() => setMode(m)}
              disabled={m === "grok" && !grok?.enabled}>
              {m === "local" ? <Cpu size={12} /> : m === "grok" ? <Cloud size={12} /> : <Sparkles size={12} />}
              {m === "local" ? "Local Qwen" : m === "grok" ? "Grok Cloud" : "Hybrid"}
            </button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-1.5 text-[10px]">
          <Tag tone="ok">LOCAL</Tag><span className="text-muted">Qwen</span>
          <StateBadge state={qwen?.state} title={qwen?.detail ?? undefined} />
          {qwen?.last?.tokens_per_s != null && <span className="num text-muted">{qwen.last.tokens_per_s.toFixed(1)} tok/s</span>}
          {qwen?.busy && <Tag tone="warn">BUSY</Tag>}
          <span className="mx-1 text-dim">|</span>
          <Tag tone="blue">CLOUD</Tag><span className="text-muted">Grok</span>
          <StateBadge state={grok?.state} title={grok?.detail ?? undefined} />
          {grok?.enabled && <span className="num text-muted">{grok.daily_used}/{grok.daily_limit} today</span>}
        </div>
        {qwen && qwen.state !== "ready" && mode !== "grok" && (
          <div className="text-[10.5px] text-warn">Local model {qwen.state}{qwen.detail ? `: ${qwen.detail}` : ""}. Answers fall back to a deterministic telemetry digest.</div>
        )}
        {anomalies.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {anomalies.slice(0, 4).map((a, i) => <Tag key={i} tone={a.tone} title={a.text}><ShieldAlert size={10} />{a.text.slice(0, 48)}</Tag>)}
          </div>
        )}
      </div>
      <div ref={scroller} className="min-h-0 flex-1 space-y-3 overflow-y-auto px-3 py-2 text-[12.5px]">
        {messages.length === 0 && (
          <div className="space-y-2 text-muted">
            <p>Ask about live telemetry. Answers are grounded in timestamped sensor data and tagged as
              <Tag tone="ok">MEASURED</Tag> <Tag tone="warn">INTERPRETATION</Tag> or <Tag tone="muted">UNAVAILABLE</Tag>.</p>
            <div className="flex flex-wrap gap-1">
              {QUICK.map((q) => <button key={q} className="btn btn-sm" onClick={() => submit(q)}>{q}</button>)}
            </div>
          </div>
        )}
        {messages.map((m) => (
          <div key={m.id} className={m.role === "user" ? "ml-6" : "mr-2"}>
            {m.role === "user" ? (
              <div className="rounded-md border border-line-2 bg-[#0e1b2f] px-2.5 py-1.5">{m.content}</div>
            ) : (
              <div className="rounded-md border border-line bg-[#060c17] px-2.5 py-2">
                <div className="mb-1 flex flex-wrap items-center gap-1.5">
                  {m.local === false ? <Tag tone="blue"><Cloud size={10} />{m.label ?? "Grok"}</Tag> : <Tag tone="ok"><Cpu size={10} />{m.label ?? "Local"}</Tag>}
                  {m.telemetry_shared === false && <Tag tone="muted">NO TELEMETRY SHARED</Tag>}
                  {m.image_sent && <Tag tone="cyan">IMAGE SENT</Tag>}
                  {m.sanitized && <Tag tone="warn" title="A shell/command block was removed from the model output">FILTERED</Tag>}
                  {m.streaming && <span className="blink text-cyan">▍</span>}
                  {!m.streaming && m.content && (
                    <button className="btn btn-sm btn-ghost ml-auto !px-1" title="Copy response"
                      onClick={() => void copyText(m.content).then((ok) => toast(ok ? "Copied" : "Copy failed", ok ? "ok" : "error"))}>
                      <Copy size={11} />
                    </button>
                  )}
                </div>
                {m.content ? <RichText text={m.content} /> : !m.error && <span className="text-muted">thinking…</span>}
                {m.error && <div className="mt-1 text-[11px] text-crit">⚠ {m.error}</div>}
                {m.notes && m.notes.length > 0 && <ul className="mt-1 text-[10.5px] text-dim">{m.notes.map((n, i) => <li key={i}>· {n}</li>)}</ul>}
                <StatsLine s={m.stats} />
              </div>
            )}
          </div>
        ))}
      </div>
      <div className="space-y-1.5 border-t border-line px-3 py-2">
        {snapshot && (
          <div className="flex items-center gap-2 text-[11px]">
            <img src={`/api/camera/snapshots/${encodeURIComponent(snapshot)}`} alt="attached snapshot" className="h-9 rounded border border-line" />
            <span className="truncate text-muted">{targetVision ? "will be sent as image input" : "model is text-only → analysed locally with OpenCV; image not sent"}</span>
            <button className="btn btn-sm btn-ghost" onClick={() => setSnapshot(null)}><X size={11} /></button>
          </div>
        )}
        <form className="flex gap-1.5" onSubmit={(e) => { e.preventDefault(); submit(input); }}>
          <input className="input min-w-0 flex-1" placeholder={busy ? "Generating…" : "Ask about telemetry, camera, IMU…"} value={input}
            maxLength={4000} onChange={(e) => setInput(e.target.value)} disabled={busy} aria-label="Message" />
          {busy ? (
            <button type="button" className="btn btn-danger" onClick={() => abort.current?.abort()} title="Stop"><Square size={12} /></button>
          ) : (
            <button type="submit" className="btn btn-primary" disabled={!input.trim()} title="Send"><Send size={13} /></button>
          )}
        </form>
        <div className="flex flex-wrap items-center gap-1.5">
          <button className="btn btn-sm" onClick={attachSnapshot} disabled={busy || !st?.camera?.available} title="Capture and attach a camera snapshot">
            <Camera size={11} /> Attach snapshot
          </button>
          {mode === "hybrid" && (
            <button className="btn btn-sm" disabled={busy || !input.trim() || !grok?.enabled} onClick={() => submit(input, true)} title="Send to Grok for deeper analysis (asks for consent)">
              <Cloud size={11} /> Deep analysis (Grok)
            </button>
          )}
          {mode !== "local" && <Toggle checked={shareTelemetry} onChange={setShareTelemetry} label="Share telemetry with Grok" />}
          <button className="btn btn-sm btn-ghost ml-auto" onClick={clear} title="Clear chat"><Trash2 size={11} /></button>
        </div>
      </div>

      {consent && (
        <div className="absolute inset-0 z-20 flex items-center justify-center bg-black/70 p-3">
          <div className="panel w-full max-w-sm space-y-2 p-3 text-[12px]">
            <div className="flex items-center gap-2 text-warn"><Cloud size={14} /><b>Send to xAI Grok (cloud)?</b></div>
            <p className="text-muted">This request leaves the Raspberry Pi. It will include:</p>
            <ul className="ml-4 list-disc space-y-0.5">
              <li>Your question</li>
              <li>{shareTelemetry ? "A timestamped telemetry summary (IMU, DHT11, servo, system, detector results)" : <span className="text-muted">No telemetry (sharing disabled)</span>}</li>
              <li>{snapshot ? (grok?.vision_capable ? "The attached camera snapshot (image)" : "Snapshot analysed locally; image not sent (model text-only)") : <span className="text-muted">No image</span>}</li>
            </ul>
            <p className="text-[11px] text-muted">Live video and location are never sent. Rate limit: {grok?.daily_used ?? 0}/{grok?.daily_limit ?? "?"} requests today.</p>
            <div className="flex justify-end gap-2">
              <button className="btn" onClick={() => setConsent(null)}>Cancel</button>
              <button className="btn btn-primary" onClick={() => { const c = consent; setConsent(null); void send(c.text, c.deep, true); }}>
                <Check size={12} /> Send to Grok
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
