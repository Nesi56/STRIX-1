export const fmt = (v: number | null | undefined, digits = 1, unit = ""): string =>
  v === null || v === undefined || Number.isNaN(v) ? "—" : `${v.toFixed(digits)}${unit}`;

export const signed = (v: number | null | undefined, digits = 1, unit = ""): string =>
  v === null || v === undefined || Number.isNaN(v) ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(digits)}${unit}`;

export const cToF = (c: number): number => (c * 9) / 5 + 32;

export function fmtBytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = n;
  while (v >= 1000 && i < u.length - 1) {
    v /= 1000;
    i++;
  }
  return `${v.toFixed(i === 0 ? 0 : 1)} ${u[i]}`;
}

export function fmtRate(bps: number | null | undefined): string {
  return bps === null || bps === undefined ? "—" : `${fmtBytes(bps)}/s`;
}

export function fmtDuration(s: number | null | undefined): string {
  if (s === null || s === undefined || !Number.isFinite(s)) return "—";
  const sec = Math.max(0, Math.floor(s));
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const r = sec % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}` : `${m}:${String(r).padStart(2, "0")}`;
}

export function fmtTime(ts: number | null | undefined, withDate = false): string {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  const t = d.toLocaleTimeString([], { hour12: false });
  return withDate ? `${d.toLocaleDateString()} ${t}` : t;
}

export function fmtAge(ts: number | null | undefined, nowS: number): string {
  if (!ts) return "—";
  const a = Math.max(0, nowS - ts);
  if (a < 1) return "now";
  if (a < 60) return `${a.toFixed(0)}s ago`;
  if (a < 3600) return `${Math.floor(a / 60)}m ago`;
  return `${Math.floor(a / 3600)}h ago`;
}
