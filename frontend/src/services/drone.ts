// Which drone the console talks to.
//
// Direct mode (dashboard served by a drone's own Pi): every path stays as is.
// Hub mode (dashboard served by the STRIX-1 hub): drone paths are sent through
// `/d/<id>/...`, and the hub adds that drone's token. Login and the hub's own
// routes (`/api/auth/*`, `/api/hub/*`) are never prefixed.

let base = "";

export function setDrone(id: string | null): void {
  base = id ? `/d/${encodeURIComponent(id)}` : "";
}

export function droneBase(): string {
  return base;
}

/** Path for a drone API route, e.g. droneUrl("/api/servo") → "/d/alpha/api/servo" in hub mode. */
export function droneUrl(path: string, prefix: string = base): string {
  if (!prefix) return path;
  if (path.startsWith("/api/auth/") || path.startsWith("/api/hub/")) return path;
  if (path.startsWith("/api/") || path.startsWith("/ws/")) return prefix + path;
  return path;
}

/** Route in the address bar: "#/" is the fleet, "#/d/<id>" one drone's console. */
export function parseRoute(hash: string): { view: "fleet" } | { view: "drone"; id: string } {
  const m = /^#\/d\/([a-z0-9-]{1,32})\/?$/.exec(hash);
  return m ? { view: "drone", id: m[1] } : { view: "fleet" };
}
