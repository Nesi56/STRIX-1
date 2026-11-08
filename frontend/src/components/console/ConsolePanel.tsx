import type { ReactNode } from "react";

/** Console-style panel: small uppercase header (optionally highlighted) with a status dot. */
export function ConsolePanel(props: {
  title: string; highlight?: boolean; status?: { ok: boolean; label?: string } | null; actions?: ReactNode;
  children: ReactNode; className?: string; bodyClassName?: string;
}) {
  return (
    <section className={`panel flex min-h-0 min-w-0 flex-col ${props.className ?? ""}`}>
      <header className="panel-head flex items-center gap-2 px-2.5 py-1.5">
        <h2 className={`text-[10.5px] font-semibold uppercase tracking-[0.14em] ${props.highlight ? "rounded-[3px] border border-accent-dim bg-[#173a27] px-1.5 py-[1px] text-[#b9f5d1]" : "text-text"}`}>
          {props.title}
        </h2>
        <div className="ml-auto flex items-center gap-2">
          {props.actions}
          {props.status && (
            <span className={`flex items-center gap-1 text-[10px] uppercase tracking-wider ${props.status.ok ? "text-accent" : "text-crit"}`}>
              <span className={`h-1.5 w-1.5 rounded-full ${props.status.ok ? "bg-accent" : "bg-crit"}`} />
              {props.status.label ?? (props.status.ok ? "OK" : "OFF")}
            </span>
          )}
        </div>
      </header>
      <div className={`min-h-0 flex-1 overflow-auto p-2.5 ${props.bodyClassName ?? ""}`}>{props.children}</div>
    </section>
  );
}
