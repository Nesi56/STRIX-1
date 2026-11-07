// Thin fetch wrapper around the STRIX-1 REST API. Authentication uses the
// HttpOnly session cookie set by POST /api/auth/login (same-origin), so no
// token is ever stored in JavaScript.

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public details?: unknown) {
    super(message);
  }
}

const unauthorizedListeners = new Set<() => void>();
export function onUnauthorized(fn: () => void): () => void {
  unauthorizedListeners.add(fn);
  return () => unauthorizedListeners.delete(fn);
}
export function emitUnauthorized(): void {
  unauthorizedListeners.forEach((fn) => fn());
}

async function parseError(r: Response): Promise<ApiError> {
  let code = `http_${r.status}`;
  let message = r.statusText || `HTTP ${r.status}`;
  let details: unknown;
  try {
    const body = await r.json();
    if (body?.error) {
      code = body.error.code ?? code;
      message = body.error.message ?? message;
      details = body.error.details;
    }
  } catch {
    /* non-JSON error */
  }
  return new ApiError(r.status, code, message, details);
}

export async function api<T>(path: string, init: { method?: string; body?: unknown; signal?: AbortSignal } = {}): Promise<T> {
  const r = await fetch(path, {
    method: init.method ?? "GET",
    credentials: "same-origin",
    headers: init.body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
    signal: init.signal,
  });
  if (r.status === 401 && !path.startsWith("/api/auth/")) emitUnauthorized();
  if (!r.ok) throw await parseError(r);
  if (r.status === 204) return undefined as T;
  return (await r.json()) as T;
}

export const get = <T>(p: string, signal?: AbortSignal) => api<T>(p, { signal });
export const post = <T>(p: string, body?: unknown) => api<T>(p, { method: "POST", body: body ?? {} });
export const patch = <T>(p: string, body: unknown) => api<T>(p, { method: "PATCH", body });
export const del = <T>(p: string) => api<T>(p, { method: "DELETE" });

/** POST a JSON body and iterate over newline-delimited JSON events. */
export async function* streamNdjson<T>(path: string, body: unknown, signal?: AbortSignal): AsyncGenerator<T> {
  const r = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (r.status === 401) emitUnauthorized();
  if (!r.ok || !r.body) throw await parseError(r);
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i: number;
    while ((i = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, i).trim();
      buf = buf.slice(i + 1);
      if (line) yield JSON.parse(line) as T;
    }
  }
  if (buf.trim()) yield JSON.parse(buf) as T;
}

export const errorMessage = (e: unknown): string =>
  e instanceof ApiError ? e.message : e instanceof Error ? e.message : String(e);
