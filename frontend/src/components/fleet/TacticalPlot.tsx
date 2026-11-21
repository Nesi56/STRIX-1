import { centroid, niceExtent, toLocal } from "../../lib/geo";
import type { FleetDrone } from "../../types/fleet";

/**
 * Where the drones are, from each drone's own GPS (3D fix only; simulated GPS is labelled).
 * Drawn locally (no map tiles), so it works with no internet. North is up; rings show distance.
 */
export function TacticalPlot({ drones, colors }: { drones: FleetDrone[]; colors: Record<string, string> }) {
  const placed = drones.filter((d) => d.online && d.position);
  if (placed.length === 0) return null;
  const origin = centroid(placed.map((d) => d.position!));
  const pts = placed.map((d) => ({ d, ...toLocal(d.position!, origin) }));
  const extent = niceExtent(Math.max(...pts.map((p) => Math.hypot(p.e, p.n))));
  const R = 100; // svg units for `extent` metres
  const k = R / extent;
  const simulated = placed.some((d) => d.position!.synthetic);

  return (
    <div className="px-3 pb-3">
      <div className="tick-label mb-1.5 flex items-center justify-between">
        <span>Positions</span>
        <span className={simulated ? "!text-synth" : "!text-ok"}>{simulated ? "SIM GPS" : "GPS"}</span>
      </div>
      <svg viewBox="-112 -112 224 224" className="w-full border border-line bg-[#0a0e11]" role="img"
        aria-label={`Map of ${placed.length} drone${placed.length === 1 ? "" : "s"}, ${extent} m to the edge`}>
        {[1, 0.5].map((f) => (
          <circle key={f} r={R * f} fill="none" stroke="#1c242c" strokeWidth={1} />
        ))}
        <line x1={-R} x2={R} y1={0} y2={0} stroke="#1c242c" />
        <line y1={-R} y2={R} x1={0} x2={0} stroke="#1c242c" />
        <text x={0} y={-R - 3} textAnchor="middle" fontSize={9} fill="#8593a0" fontFamily="var(--font-mono)">N</text>
        <text x={R * 0.5 + 2} y={-3} fontSize={7} fill="#55626e" fontFamily="var(--font-mono)">{extent / 2} m</text>
        <text x={R + 2} y={-3} fontSize={7} fill="#55626e" fontFamily="var(--font-mono)" textAnchor="end">{extent} m</text>
        {pts.map(({ d, e, n }) => {
          const c = colors[d.id] ?? "#6fd3ff";
          const x = e * k;
          const y = -n * k;
          const hdg = d.flight?.heading;
          const alt = d.position!.alt_rel_m;
          return (
            <g key={d.id} transform={`translate(${x.toFixed(1)} ${y.toFixed(1)})`}>
              <title>{`${d.name}: ${d.position!.lat.toFixed(6)}, ${d.position!.lon.toFixed(6)}${alt != null ? `, ${alt.toFixed(1)} m above home` : ""}`}</title>
              {hdg != null ? (
                <path d="M0 -8 L5.5 6 L0 3 L-5.5 6 Z" fill={c} stroke="#07090b" strokeWidth={1} transform={`rotate(${hdg})`} />
              ) : (
                <circle r={4.5} fill={c} stroke="#07090b" />
              )}
              <text x={8} y={-4} fontSize={9} fill="#dfe6ec" fontFamily="var(--font-sans)" fontWeight={600}>{d.name.toUpperCase()}</text>
              {alt != null && <text x={8} y={6} fontSize={7.5} fill="#8593a0" fontFamily="var(--font-mono)">{alt.toFixed(0)} m</text>}
            </g>
          );
        })}
      </svg>
    </div>
  );
}
