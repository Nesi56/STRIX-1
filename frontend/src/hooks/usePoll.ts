import { useCallback, useEffect, useRef, useState } from "react";
import { get } from "../services/api";

/** Poll a GET endpoint. Returns data, error, and a manual refresh function. */
export function usePoll<T>(path: string | null, intervalMs = 5000) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const alive = useRef(true);
  const refresh = useCallback(async () => {
    if (!path) return;
    try {
      const d = await get<T>(path);
      if (alive.current) {
        setData(d);
        setError(null);
      }
    } catch (e) {
      if (alive.current) setError(e instanceof Error ? e.message : String(e));
    }
  }, [path]);
  useEffect(() => {
    alive.current = true;
    refresh();
    if (!intervalMs) return () => { alive.current = false; };
    const id = window.setInterval(refresh, intervalMs);
    return () => {
      alive.current = false;
      clearInterval(id);
    };
  }, [refresh, intervalMs]);
  return { data, error, refresh, setData };
}
