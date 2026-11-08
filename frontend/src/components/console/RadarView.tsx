import { useTelemetry } from "../../hooks/useTelemetry";
import { boxLabel } from "./VideoFeed";

/** Top-down radar of detections: bearing from image position, range from object size (estimate). */
export function RadarView() {
  const st = useTelemetry();
  const det = st?.detections;
  const hfov = st?.detector?.hfov_deg ?? 102;
  const dets = det?.detections ?? [];
  const maxR = Math.max(10, Math.ceil(Math.max(0, ...dets.map((d) => d.distance_m ?? 0)) / 5) * 5);
  const R = 140;
  const cx = 160;
  const cy = 160;
  const half = (hfov / 2) * (Math.PI / 180);
  const pt = (rangeM: number, bearingDeg: number) => {
    const r = (Math.min(rangeM, maxR) / maxR) * R;
    const b = (bearingDeg * Math.PI) / 180;
    return [cx + r * Math.sin(b), cy - r * Math.cos(b)];
  };
  const rings = [0.25, 0.5, 0.75, 1].map((f) => f * maxR);
  const pos = st?.mapping?.position;
  return (
    <div className="flex h-full flex-col">
      <svg viewBox="0 0 320 175" className="w-full">
        <path d={`M ${cx} ${cy} L ${cx + R * Math.sin(-half)} ${cy - R * Math.cos(-half)} A ${R} ${R} 0 0 1 ${cx + R * Math.sin(half)} ${cy - R * Math.cos(half)} Z`}
          fill="#3ddc8410" stroke="#1e6e45" />
        {rings.map((r) => (
          <g key={r}>
            <path d={`M ${cx - (r / maxR) * R} ${cy} A ${(r / maxR) * R} ${(r / maxR) * R} 0 0 1 ${cx + (r / maxR) * R} ${cy}`} fill="none" stroke="#222824" />
            <text x={cx + 3} y={cy - (r / maxR) * R + 9} fontSize="7" fill="#5a655e">{r.toFixed(0)} m</text>
          </g>
        ))}
        {[-90, -45, 0, 45, 90].map((a) => {
          const [x, y] = pt(maxR, a);
          return <line key={a} x1={cx} y1={cy} x2={x} y2={y} stroke="#222824" strokeDasharray="2 3" />;
        })}
        <polygon points={`${cx},${cy - 7} ${cx - 5},${cy + 4} ${cx + 5},${cy + 4}`} fill="#3ddc84" />
        {dets.map((d, i) => {
          const known = d.distance_m != null;
          const [x, y] = pt(known ? d.distance_m! : maxR, d.bearing_deg);
          return (
            <g key={i}>
              <circle cx={x} cy={y} r={4} fill={known ? "#39ff7a" : "none"} stroke="#39ff7a" strokeWidth={1.5} />
              <text x={x + 6} y={y - 4} fontSize="7.5" fill="#d6dfd9">{known ? boxLabel(d).replace(/ 0\.\d+/, "") : `${d.label} ?`}</text>
            </g>
          );
        })}
        <text x="4" y="170" fontSize="7" fill="#5a655e">camera FOV ±{(hfov / 2).toFixed(0)}° · tilt {st?.servo?.tilt_deg != null ? `${st.servo.tilt_deg.toFixed(0)}°` : "—"}</text>
      </svg>
      <div className="mt-1 space-y-1 overflow-auto text-[10.5px]">
        {dets.length === 0 && <div className="text-dim">No prompted objects in view.</div>}
        {dets.map((d, i) => (
          <div key={i} className="flex justify-between gap-2 border-b border-line pb-0.5">
            <span className="text-text">{d.label} <span className="text-dim">{d.confidence.toFixed(2)}</span></span>
            <span className="text-muted">
              {d.distance_m == null ? "range n/a" : d.distance_kind === "upper_bound" ? `≤${d.distance_m.toFixed(1)} m` : `~${d.distance_m.toFixed(1)} m`}
              {" · "}{Math.abs(d.bearing_deg) < 1 ? "ahead" : `${Math.abs(d.bearing_deg).toFixed(0)}° ${d.bearing_deg > 0 ? "right" : "left"}`}
            </span>
          </div>
        ))}
        <p className="pt-1 text-dim">Range is estimated from typical object size (pinhole model, ±30 % or worse); bearing from image position. {pos?.message}</p>
      </div>
    </div>
  );
}
