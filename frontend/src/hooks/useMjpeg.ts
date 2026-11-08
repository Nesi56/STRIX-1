// Fetch-based MJPEG reader: always draws the newest frame (stale frames are dropped so
// latency stays bounded), measures received FPS, bitrate and glass-to-browser latency
// from the X-Capture-Time header plus the WebSocket clock offset.
import { useEffect, useState, type RefObject } from "react";
import { telemetry } from "./useTelemetry";
import { errorMessage } from "../services/api";
import { MultipartJpegParser } from "../services/mjpeg";

export interface StreamMetrics {
  fps: number;
  latencyMs: number | null;
  kbps: number;
  synthetic: boolean;
  error: string | null;
  frames: number;
}

export function useMjpeg(canvas: RefObject<HTMLCanvasElement | null>, enabled: boolean, maxFps = 0, key = 0): StreamMetrics {
  const [m, setM] = useState<StreamMetrics>({ fps: 0, latencyMs: null, kbps: 0, synthetic: false, error: null, frames: 0 });
  useEffect(() => {
    if (!enabled) return;
    const ac = new AbortController();
    const counters = { frames: 0, bytes: 0, latSum: 0, latN: 0, synthetic: false };
    let pending: { jpeg: Uint8Array; captureTs: number; synthetic: boolean } | null = null;
    let decoding = false;
    let lastErr: string | null = null;

    const draw = async () => {
      if (decoding || !pending) return;
      decoding = true;
      const f = pending;
      pending = null;
      try {
        const bmp = await createImageBitmap(new Blob([f.jpeg as BlobPart], { type: "image/jpeg" }));
        const c = canvas.current;
        if (c) {
          if (c.width !== bmp.width || c.height !== bmp.height) {
            c.width = bmp.width;
            c.height = bmp.height;
          }
          c.getContext("2d")?.drawImage(bmp, 0, 0);
        }
        bmp.close();
        counters.frames++;
        counters.synthetic = f.synthetic;
        if (f.captureTs > 0) {
          const lat = telemetry.clock.serverNow() * 1000 - f.captureTs * 1000;
          if (lat > -500 && lat < 30000) {
            counters.latSum += Math.max(0, lat);
            counters.latN++;
          }
        }
      } catch {
        /* corrupt frame — skip */
      } finally {
        decoding = false;
        if (pending) void draw();
      }
    };

    const run = async () => {
      while (!ac.signal.aborted) {
        try {
          const url = `/api/camera/stream.mjpg${maxFps ? `?fps=${maxFps}` : ""}`;
          const r = await fetch(url, { signal: ac.signal, credentials: "same-origin", cache: "no-store" });
          if (!r.ok || !r.body) throw new Error(`stream HTTP ${r.status}`);
          lastErr = null;
          const reader = r.body.getReader();
          const parser = new MultipartJpegParser();
          for (;;) {
            const { value, done } = await reader.read();
            if (done) break;
            counters.bytes += value.length;
            const frames = parser.push(value);
            if (frames.length) {
              pending = frames[frames.length - 1];
              void draw();
            }
          }
          throw new Error("stream ended");
        } catch (e) {
          if (ac.signal.aborted) return;
          lastErr = errorMessage(e);
          await new Promise((res) => setTimeout(res, 1500)); // auto-reconnect
        }
      }
    };
    void run();
    const tick = window.setInterval(() => {
      // Snapshot before resetting: React may run the updater function later (lazily).
      const snap = {
        fps: counters.frames,
        latencyMs: counters.latN ? counters.latSum / counters.latN : null,
        kbps: (counters.bytes * 8) / 1000,
        synthetic: counters.synthetic,
        error: lastErr,
      };
      counters.frames = counters.bytes = counters.latSum = counters.latN = 0;
      setM((p) => ({ ...snap, frames: p.frames + snap.fps }));
    }, 1000);
    return () => {
      ac.abort();
      clearInterval(tick);
    };
  }, [canvas, enabled, maxFps, key]);
  return m;
}
