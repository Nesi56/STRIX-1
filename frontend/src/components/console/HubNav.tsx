import { LayoutGrid } from "lucide-react";
import { droneColor } from "../../pages/FleetPage";
import type { FleetResponse } from "../../types/fleet";

/** In the hub: back to the fleet, and one tab per drone. */
export function HubNav({ current, fleet }: { current: string; fleet: FleetResponse | null }) {
  const drones = fleet?.drones ?? [];
  return (
    <nav className="flex min-w-0 items-center gap-1 overflow-x-auto" aria-label="Drones">
      <a href="#/" className="btn btn-sm shrink-0" title="All drones"><LayoutGrid size={12} /> Fleet</a>
      {drones.map((d, i) => {
        const c = droneColor(d, i);
        const active = d.id === current;
        return (
          <a key={d.id} href={`#/d/${d.id}`} aria-current={active ? "page" : undefined}
            className={`flex shrink-0 items-center gap-1.5 rounded-md border px-2 py-1 text-[11px] tracking-wider ${active ? "border-line-2 bg-panel-2 text-text" : "border-transparent text-muted hover:text-text"}`}
            style={active ? { boxShadow: `inset 0 -2px 0 ${c}` } : undefined}
            title={d.online ? `${d.name} is online` : `${d.name} is offline${d.error ? `: ${d.error}` : ""}`}>
            <span className="h-2 w-2 rounded-full" style={{ background: d.online ? c : "#5a655e" }} />
            {d.name}
          </a>
        );
      })}
    </nav>
  );
}
