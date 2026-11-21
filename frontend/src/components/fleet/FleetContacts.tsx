import type { FleetContact } from "../../types/fleet";

/**
 * What the whole fleet sees right now: every drone's detections merged by object class, with the
 * drones that see each one and the nearest estimated range. Observation only: the operator decides.
 */
export function FleetContacts({ contacts, online }: { contacts: FleetContact[]; online: number }) {
  return (
    <div className="px-3 pb-3">
      <div className="tick-label mb-1.5 flex items-center justify-between">
        <span>Fleet contacts</span>
        <span className="num text-dim">{contacts.reduce((a, c) => a + c.count, 0)}</span>
      </div>
      {contacts.length === 0 ? (
        <div className="border border-line px-2.5 py-2 text-[11.5px] text-dim">
          {online ? "Nothing in view on any drone." : "No drone online."}
        </div>
      ) : (
        <ul className="border border-line">
          {contacts.map((c) => (
            <li key={c.label} className="flex items-center gap-2 border-b border-line px-2.5 py-1.5 last:border-0">
              <span className="num w-7 shrink-0 text-right text-[13px] text-text">{c.count}×</span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[13px] font-semibold tracking-[0.06em] text-text">{c.label.toUpperCase()}</span>
                <span className="block truncate text-[10.5px] tracking-[0.1em] text-muted" title="Drones that see it">
                  {c.drones.map((d) => d.name.toUpperCase()).join(" · ")}
                </span>
              </span>
              <span className="num shrink-0 text-[11.5px] text-muted" title="Nearest estimated range">
                {c.nearest_m == null ? "—" : `~${c.nearest_m.toFixed(c.nearest_m < 10 ? 1 : 0)} m`}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
