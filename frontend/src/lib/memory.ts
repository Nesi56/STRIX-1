// Pure helpers for the object-memory views (3D world, list, video overlay). Unit-tested.
import type { MemoryObject, MemoryState } from "../types/telemetry";

export const MEM_COLOR = { seen: "#39ff7a", remembered: "#9fb3c8", hidden: "#ffb020" } as const;

/** Seconds out of sight, advanced to "now" on the server clock between memory updates. */
export function outOfSight(o: MemoryObject, mem: MemoryState, serverNow: number): number {
  if (o.memory === "seen") return 0;
  return o.out_of_sight_s + Math.max(0, serverNow - mem.ts);
}

export function fmtAgo(s: number): string {
  if (s < 60) return `${s.toFixed(s < 10 ? 1 : 0)} s`;
  const m = Math.floor(s / 60);
  return `${m} min ${Math.floor(s % 60)} s`;
}

/** One-line status used in the list, the 3D labels and the video overlay. */
export function stateText(o: MemoryObject, mem: MemoryState, serverNow: number): string {
  const t = fmtAgo(outOfSight(o, mem, serverNow));
  if (o.memory === "seen") return "SEEN";
  if (o.memory === "hidden") return `HIDDEN under ${o.contained_by_name ?? "?"} · ${t}`;
  return `REMEMBERED · ${o.state === "out_of_view" ? "out of view" : "not detected"} · ${t}`;
}

/** Compact status for 3D labels. */
export function stateShort(o: MemoryObject, mem: MemoryState, serverNow: number): string {
  const t = fmtAgo(outOfSight(o, mem, serverNow));
  if (o.memory === "seen") return "SEEN";
  if (o.memory === "hidden") return `HIDDEN in ${o.contained_by_name ?? "?"} · ${t}`;
  return `REMEMBERED · ${t}`;
}

/** Distances beyond NEAR are compressed so close (table-top) and far (outdoor) objects fit one view. */
export const NEAR_M = 1.5;
export function displayScale(r: number): number {
  if (r <= NEAR_M) return 1;
  return (NEAR_M + (r - NEAR_M) * 0.4) / r;
}

/** Memory frame (x right, y up, z forward) → three.js (forward = −z), with distance compression. */
export function toScene(xyz: [number, number, number]): { pos: [number, number, number]; k: number } {
  const [x, y, z] = xyz;
  const r = Math.hypot(x, y, z) || 1;
  const k = displayScale(r);
  return { pos: [x * k, y * k, -z * k], k };
}

/** Physical size (m) from angular size and range (2 m when the range is unknown). */
export function sizeM(o: MemoryObject): { w: number; h: number } {
  const r = o.range_m ?? 2;
  const ang = (d: number) => 2 * r * Math.tan(((d / 2) * Math.PI) / 180);
  return { w: Math.max(0.02, ang(o.w_deg)), h: Math.max(0.02, ang(o.h_deg)) };
}

/** Unit vector (three.js frame) for a direction given as azimuth/elevation in degrees. */
export function dir(azDeg: number, elDeg: number): [number, number, number] {
  const a = (azDeg * Math.PI) / 180;
  const e = (elDeg * Math.PI) / 180;
  return [Math.cos(e) * Math.sin(a), Math.sin(e), -Math.cos(e) * Math.cos(a)];
}

export const ROUND = new Set(["sports ball", "orange", "apple", "donut", "clock"]);
