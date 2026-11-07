import type { ReactNode } from "react";
import type { DeviceState } from "../../types/telemetry";

export function Panel(props: {
  title: string;
  icon?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
  badge?: ReactNode;
}) {
  return (
    <section className={`panel corner relative flex min-w-0 flex-col ${props.className ?? ""}`}>
      <header className="panel-head flex items-center gap-2 rounded-t-[10px] px-3 py-2">
        {props.icon && <span className="text-cyan">{props.icon}</span>}
        <h2 className="label !text-[11px] !text-text">{props.title}</h2>
        {props.badge}
        <div className="ml-auto flex items-center gap-1.5">{props.actions}</div>
      </header>
      <div className={`min-h-0 flex-1 p-3 ${props.bodyClassName ?? ""}`}>{props.children}</div>
    </section>
  );
}

const STATE_STYLE: Record<string, string> = {
  ok: "bg-ok/15 text-ok border-ok/40",
  synthetic: "bg-synth/15 text-synth border-synth/40",
  degraded: "bg-warn/15 text-warn border-warn/40",
  starting: "bg-blue/15 text-blue border-blue/40",
  disconnected: "bg-crit/15 text-crit border-crit/40",
  error: "bg-crit/15 text-crit border-crit/40",
  disabled: "bg-dim/15 text-muted border-dim/40",
  ready: "bg-ok/15 text-ok border-ok/40",
  loading: "bg-warn/15 text-warn border-warn/40",
  unreachable: "bg-crit/15 text-crit border-crit/40",
  not_configured: "bg-dim/15 text-muted border-dim/40",
  unknown: "bg-dim/15 text-muted border-dim/40",
};

const STATE_LABEL: Record<string, string> = {
  ok: "LIVE",
  synthetic: "SYNTHETIC",
  degraded: "DEGRADED",
  starting: "STARTING",
  disconnected: "DISCONNECTED",
  error: "ERROR",
  disabled: "DISABLED",
  ready: "READY",
  loading: "LOADING",
  unreachable: "UNREACHABLE",
  not_configured: "NOT CONFIGURED",
  unknown: "UNKNOWN",
};

export function StateBadge({ state, label, title }: { state: DeviceState | string | undefined; label?: string; title?: string }) {
  const s = state ?? "unknown";
  return (
    <span title={title} className={`num inline-flex items-center gap-1 rounded border px-1.5 py-[1px] text-[10px] tracking-wider ${STATE_STYLE[s] ?? STATE_STYLE.unknown}`}>
      <span className="h-1.5 w-1.5 rounded-full bg-current" />
      {label ?? STATE_LABEL[s] ?? s.toUpperCase()}
    </span>
  );
}

export function Tag({ children, tone = "cyan", title }: { children: ReactNode; tone?: "cyan" | "warn" | "crit" | "ok" | "synth" | "muted" | "blue"; title?: string }) {
  const tones: Record<string, string> = {
    cyan: "border-cyan/40 text-cyan bg-cyan/10",
    blue: "border-blue/40 text-blue bg-blue/10",
    warn: "border-warn/40 text-warn bg-warn/10",
    crit: "border-crit/40 text-crit bg-crit/10",
    ok: "border-ok/40 text-ok bg-ok/10",
    synth: "border-synth/40 text-synth bg-synth/10",
    muted: "border-line-2 text-muted bg-white/[.02]",
  };
  return (
    <span title={title} className={`num inline-flex items-center gap-1 rounded border px-1.5 py-[1px] text-[10px] tracking-wider ${tones[tone]}`}>
      {children}
    </span>
  );
}

export function Stat(props: { label: string; value: ReactNode; unit?: string; sub?: ReactNode; tone?: "default" | "warn" | "crit" | "ok"; big?: boolean }) {
  const tone = { default: "text-text", warn: "text-warn", crit: "text-crit", ok: "text-ok" }[props.tone ?? "default"];
  return (
    <div className="min-w-0">
      <div className="label truncate">{props.label}</div>
      <div className={`num ${props.big ? "text-2xl" : "text-base"} leading-tight ${tone}`}>
        {props.value}
        {props.unit && <span className="ml-0.5 text-[11px] text-muted">{props.unit}</span>}
      </div>
      {props.sub && <div className="truncate text-[11px] text-muted">{props.sub}</div>}
    </div>
  );
}

export function Bar({ value, max = 100, tone }: { value: number | null | undefined; max?: number; tone?: string }) {
  const v = Math.max(0, Math.min(1, (value ?? 0) / max));
  const color = tone ?? (v > 0.9 ? "bg-crit" : v > 0.75 ? "bg-warn" : "bg-cyan");
  return (
    <div className="h-1.5 w-full overflow-hidden rounded bg-white/5">
      <div className={`h-full ${color} transition-[width] duration-500`} style={{ width: `${v * 100}%` }} />
    </div>
  );
}

export function Toggle({ checked, onChange, label, disabled }: { checked: boolean; onChange: (v: boolean) => void; label?: string; disabled?: boolean }) {
  return (
    <label className={`inline-flex cursor-pointer select-none items-center gap-2 ${disabled ? "opacity-50" : ""}`}>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={`relative h-4 w-7 rounded-full border transition ${checked ? "border-cyan/60 bg-cyan/30" : "border-line-2 bg-white/5"}`}
      >
        <span className={`absolute top-[1px] h-3 w-3 rounded-full transition ${checked ? "left-[13px] bg-cyan" : "left-[1px] bg-muted"}`} />
      </button>
      {label && <span className="text-[12px]">{label}</span>}
    </label>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="flex h-full min-h-16 items-center justify-center p-3 text-center text-[12px] text-muted">{children}</div>;
}

export function Notice({ tone = "info", children }: { tone?: "info" | "warn" | "crit" | "synth"; children: ReactNode }) {
  const t = {
    info: "border-blue/30 bg-blue/5 text-[#b9cff5]",
    warn: "border-warn/30 bg-warn/5 text-warn",
    crit: "border-crit/40 bg-crit/10 text-crit",
    synth: "border-synth/40 bg-synth/10 text-synth",
  }[tone];
  return <div className={`rounded-md border px-2.5 py-1.5 text-[12px] ${t}`}>{children}</div>;
}
