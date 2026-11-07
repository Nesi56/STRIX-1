// Telemetry store: one WebSocket per tab, automatic reconnect with backoff,
// clock-offset sync, ring buffers for charts. React components subscribe via
// useSyncExternalStore and are notified at most ~10×/s; the 3D view reads the
// newest IMU sample directly every animation frame.
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { ClockSync, computeSample } from "../services/clock";
import { emitUnauthorized } from "../services/api";
import { Ring } from "../lib/ring";
import type { ImuSample, TelemetryEvent, TelemetryState } from "../types/telemetry";

export type ConnState = "connecting" | "open" | "stale" | "closed" | "unauthorized";

export interface Snapshot {
  conn: ConnState;
  state: TelemetryState | null;
  lastMessageAt: number | null;
  rttMs: number | null;
  offsetMs: number;
  reconnectAttempt: number;
  events: TelemetryEvent[];
  disconnectedSince: number | null;
}

export interface ImuPoint {
  t: number; ax: number; ay: number; az: number; gx: number; gy: number; gz: number; roll: number; pitch: number; yaw: number;
}

class TelemetryStore {
  snap: Snapshot = {
    conn: "closed", state: null, lastMessageAt: null, rttMs: null, offsetMs: 0, reconnectAttempt: 0, events: [],
    disconnectedSince: null,
  };
  latestImu: ImuSample | null = null;
  imuRing = new Ring<ImuPoint>(20 * 120);
  clock = new ClockSync();
  private ws: WebSocket | null = null;
  private listeners = new Set<() => void>();
  private emitTimer: number | null = null;
  private retryTimer: number | null = null;
  private pingTimer: number | null = null;
  private watchdog: number | null = null;
  private wanted = false;
  private lastImuSeq = -1;

  subscribe = (fn: () => void) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };

  getSnapshot = () => this.snap;

  private set(patch: Partial<Snapshot>, immediate = false) {
    this.snap = { ...this.snap, ...patch };
    if (immediate) return this.flush();
    if (this.emitTimer === null) this.emitTimer = window.setTimeout(() => this.flush(), 100);
  }

  private flush() {
    if (this.emitTimer !== null) {
      clearTimeout(this.emitTimer);
      this.emitTimer = null;
    }
    this.listeners.forEach((fn) => fn());
  }

  start() {
    if (this.wanted) return;
    this.wanted = true;
    window.addEventListener("online", this.reconnectNow);
    document.addEventListener("visibilitychange", this.onVisible);
    this.open();
    this.watchdog = window.setInterval(this.checkStale, 1000);
  }

  stop() {
    this.wanted = false;
    window.removeEventListener("online", this.reconnectNow);
    document.removeEventListener("visibilitychange", this.onVisible);
    if (this.watchdog) clearInterval(this.watchdog);
    if (this.retryTimer) clearTimeout(this.retryTimer);
    this.ws?.close();
    this.ws = null;
  }

  private onVisible = () => {
    if (document.visibilityState === "visible" && (this.snap.conn === "closed" || this.snap.conn === "stale")) this.reconnectNow();
  };

  reconnectNow = () => {
    if (!this.wanted) return;
    if (this.retryTimer) clearTimeout(this.retryTimer);
    this.ws?.close();
    this.open();
  };

  private checkStale = () => {
    const last = this.snap.lastMessageAt;
    if (this.snap.conn === "open" && last && Date.now() - last > 3000) this.set({ conn: "stale" }, true);
    if (this.snap.conn === "stale" && last && Date.now() - last > 7000) this.reconnectNow();
  };

  private open() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/telemetry`);
    this.ws = ws;
    this.set({ conn: "connecting" }, true);
    ws.onopen = () => {
      this.set({ conn: "open", reconnectAttempt: 0, disconnectedSince: null }, true);
      this.ping();
      if (this.pingTimer) clearInterval(this.pingTimer);
      this.pingTimer = window.setInterval(() => this.ping(), 2000);
    };
    ws.onmessage = (ev) => this.onMessage(ev.data as string);
    ws.onclose = (ev) => {
      if (this.ws !== ws) return;
      if (this.pingTimer) clearInterval(this.pingTimer);
      if (ev.code === 4401) {
        this.set({ conn: "unauthorized" }, true);
        emitUnauthorized();
        return;
      }
      const attempt = this.snap.reconnectAttempt + 1;
      this.set({ conn: "closed", reconnectAttempt: attempt, disconnectedSince: this.snap.disconnectedSince ?? Date.now() }, true);
      if (this.wanted) {
        const delay = Math.min(10000, 500 * 2 ** Math.min(attempt, 5));
        this.retryTimer = window.setTimeout(() => this.open(), delay);
      }
    };
  }

  private ping() {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify({ type: "ping", client_ts: Date.now() }));
  }

  private onMessage(text: string) {
    let msg: Record<string, unknown>;
    try {
      msg = JSON.parse(text);
    } catch {
      return;
    }
    const now = Date.now();
    switch (msg.type) {
      case "hello": {
        const snap = msg.snapshot as TelemetryState;
        this.ingest(snap);
        this.set({ state: { ...(this.snap.state ?? ({} as TelemetryState)), ...snap }, lastMessageAt: now, conn: "open" }, true);
        break;
      }
      case "telemetry": {
        const m = msg as unknown as Partial<TelemetryState>;
        this.ingest(m);
        const st = this.snap.state ? { ...this.snap.state, ...m } : (m as TelemetryState);
        this.set({ state: st, lastMessageAt: now, conn: this.snap.conn === "stale" ? "open" : this.snap.conn });
        break;
      }
      case "pong": {
        const s = computeSample(Number(msg.client_ts), Number(msg.server_ts), now);
        this.clock.add(s);
        this.set({ rttMs: s.rttMs, offsetMs: this.clock.offsetMs, lastMessageAt: now });
        break;
      }
      case "event": {
        const events = [...this.snap.events, msg.event as TelemetryEvent].slice(-200);
        this.set({ events });
        break;
      }
    }
  }

  private ingest(m: Partial<TelemetryState>) {
    const s = m.imu?.latest;
    if (s && s.seq !== this.lastImuSeq) {
      this.lastImuSeq = s.seq;
      this.latestImu = s;
      this.imuRing.push({
        t: s.ts, ax: s.accel.x, ay: s.accel.y, az: s.accel.z, gx: s.gyro.x, gy: s.gyro.y, gz: s.gyro.z,
        roll: s.orientation.roll, pitch: s.orientation.pitch, yaw: s.orientation.yaw_relative,
      });
    }
  }
}

export const telemetry = new TelemetryStore();

export function useTelemetrySnapshot(): Snapshot {
  return useSyncExternalStore(telemetry.subscribe, telemetry.getSnapshot);
}

export function useTelemetry(): TelemetryState | null {
  return useTelemetrySnapshot().state;
}

/** Throttled copy of the IMU ring buffer for charts. */
export function useImuSeries(windowS = 30, intervalMs = 250): ImuPoint[] {
  const [data, setData] = useState<ImuPoint[]>([]);
  useEffect(() => {
    const tick = () => {
      const all = telemetry.imuRing.toArray();
      const last = all.length ? all[all.length - 1].t : 0;
      setData(all.filter((p) => p.t >= last - windowS));
    };
    tick();
    const id = window.setInterval(tick, intervalMs);
    return () => clearInterval(id);
  }, [windowS, intervalMs]);
  return data;
}

/** Current server time (seconds), refreshed every second, for "age" displays. */
export function useServerNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => telemetry.clock.serverNow());
  const ref = useRef<number>(0);
  useEffect(() => {
    ref.current = window.setInterval(() => setNow(telemetry.clock.serverNow()), intervalMs);
    return () => clearInterval(ref.current);
  }, [intervalMs]);
  return now;
}
