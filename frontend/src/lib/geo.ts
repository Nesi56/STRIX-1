// Local flat-earth projection for a few drones within a few kilometres (plenty for a field).
export interface LatLon { lat: number; lon: number }

const M_PER_DEG_LAT = 110_574;
const M_PER_DEG_LON_EQ = 111_320;

/** East/north metres of `p` from `origin`. */
export function toLocal(p: LatLon, origin: LatLon): { e: number; n: number } {
  const k = Math.cos((origin.lat * Math.PI) / 180);
  return { e: (p.lon - origin.lon) * M_PER_DEG_LON_EQ * k, n: (p.lat - origin.lat) * M_PER_DEG_LAT };
}

export function centroid(ps: LatLon[]): LatLon {
  return { lat: ps.reduce((a, p) => a + p.lat, 0) / ps.length, lon: ps.reduce((a, p) => a + p.lon, 0) / ps.length };
}

/** A round plot radius (m) that fits `maxDist` with some margin: 20, 50, 100, 200, 500, 1000, … */
export function niceExtent(maxDist: number): number {
  const need = Math.max(20, maxDist * 1.25);
  const steps = [1, 2, 5];
  for (let p = 1; ; p *= 10) {
    for (const s of steps) {
      if (s * p * 10 >= need) return s * p * 10;
    }
  }
}

export function distance(a: LatLon, b: LatLon): number {
  const { e, n } = toLocal(b, a);
  return Math.hypot(e, n);
}
