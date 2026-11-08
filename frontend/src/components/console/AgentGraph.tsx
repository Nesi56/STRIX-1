import { Check, Circle, Loader2, Minus, X } from "lucide-react";
import { useTelemetry } from "../../hooks/useTelemetry";
import type { AgentState, PlanStep } from "../../types/telemetry";

type NodeId = "generator" | "observer" | "pilot" | "until" | "report";

function Node({ id, label, agent, sub }: { id: NodeId; label: string; agent?: AgentState; sub: string }) {
  const active = agent?.status === "running" && agent.node === id;
  const doneAll = agent?.status === "done";
  // The generator always runs first, so once the loop starts it has been visited.
  const visited = agent?.status === "running" && id === "generator" && agent.node !== "generator";
  const cls = active
    ? "border-accent bg-[#173a27] text-[#d7ffe6] node-active"
    : doneAll || visited
      ? "border-accent-dim text-[#9fe7bf]"
      : "border-line-2 text-muted";
  return (
    <div className={`flex min-w-[92px] flex-col items-center rounded-[3px] border-2 px-3 py-1.5 transition ${cls}`}>
      <span className="text-[11px] font-bold tracking-[0.14em]">{label}</span>
      <span className="text-[9px] text-dim">{sub}</span>
    </div>
  );
}

const Arrow = ({ label, dir = "down" }: { label?: string; dir?: "down" | "right" }) =>
  dir === "down" ? (
    <div className="flex flex-col items-center text-dim">
      <div className="h-4 w-px bg-line-2" />
      <span className="-mt-1 text-[10px]">▼</span>
      {label && <span className="text-[9px]">{label}</span>}
    </div>
  ) : (
    <div className="flex items-center text-dim"><div className="h-px w-5 bg-line-2" /><span className="-ml-1 text-[10px]">▶</span></div>
  );

function StepIcon({ s }: { s: PlanStep["status"] }) {
  if (s === "running") return <Loader2 size={12} className="animate-spin text-accent" />;
  if (s === "done") return <Check size={12} className="text-accent" />;
  if (s === "failed") return <X size={12} className="text-crit" />;
  if (s === "skipped") return <Minus size={12} className="text-warn" />;
  return <Circle size={10} className="text-dim" />;
}

export function AgentGraph() {
  const agent = useTelemetry()?.agent;
  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-col items-center py-1">
        <Node id="generator" label="GENERATOR" sub="Qwen → JSON plan" agent={agent} />
        <Arrow label="plan" />
        <div className="w-full max-w-[340px] rounded-[3px] border border-dashed border-line-2 px-2 pb-2 pt-1">
          <div className="mb-1.5 text-center text-[9.5px] font-semibold tracking-[0.18em] text-muted">EXECUTION LOOP</div>
          <div className="flex items-center justify-center gap-1">
            <Node id="observer" label="OBSERVER" sub="sensors + YOLO" agent={agent} />
            <Arrow dir="right" />
            <Node id="pilot" label="PILOT" sub="camera servo" agent={agent} />
          </div>
          <div className="flex justify-center"><Arrow /></div>
          <div className="flex items-center justify-center gap-2">
            <Node id="until" label="UNTIL" sub="reached · found · centered" agent={agent} />
            <span className="text-[9px] text-dim">↺ next step</span>
          </div>
        </div>
        <Arrow label="complete" />
        <Node id="report" label="REPORT" sub="Qwen answer + suggestions" agent={agent} />
      </div>
      <div className="mt-2 min-h-0 flex-1 overflow-auto border-t border-line pt-2 text-[11px]">
        <div className="flex items-center gap-2">
          <span className="text-muted">STATUS</span>
          <span className={agent?.status === "running" ? "text-accent" : agent?.status === "error" || agent?.status === "stopped" ? "text-crit" : "text-text"}>
            {(agent?.status ?? "idle").toUpperCase()}
          </span>
          {agent?.planner && <span className="rounded-[3px] border border-line-2 px-1 text-[9.5px] text-muted">{agent.planner === "qwen" ? "QWEN PLAN" : "RULE PLAN (Qwen offline)"}</span>}
        </div>
        {agent?.task && <div className="mt-1 truncate text-muted">task: <span className="text-text">{agent.task}</span></div>}
        {agent?.error && agent.status !== "running" && <div className="mt-1 text-crit">{agent.error}</div>}
        <ol className="mt-1.5 space-y-1">
          {(agent?.plan ?? []).map((p, i) => (
            <li key={i} className="flex items-start gap-1.5">
              <span className="mt-0.5"><StepIcon s={p.status} /></span>
              <span>
                <span className={p.status === "running" ? "text-accent" : "text-text"}>{p.text}</span>
                {p.result && <span className="block text-[10.5px] text-dim">{p.result}</span>}
              </span>
            </li>
          ))}
          {agent && agent.plan.length === 0 && agent.status !== "idle" && <li className="text-dim">No camera motion needed.</li>}
          {(!agent || agent.status === "idle") && <li className="text-dim">Give Qwen a task in the chat below.</li>}
        </ol>
      </div>
    </div>
  );
}
