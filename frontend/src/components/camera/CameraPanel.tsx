import { Camera, Circle, Expand, Layers, RefreshCw, Square, Video } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { telemetry, useTelemetry } from "../../hooks/useTelemetry";
import { errorMessage, get, post } from "../../services/api";
import { MultipartJpegParser } from "../../services/mjpeg";
import { startWhep, webrtcLatencyMs, type WhepSession } from "../../services/whep";
import { fmt, fmtBytes, fmtDuration } from "../../lib/format";
import type { CameraStatus, Observation } from "../../types/telemetry";
import { Empty, Panel, StateBadge, Tag } from "../common/ui";
import { useToast } from "../common/toast";

interface StreamMetrics {
  fps: number;
  latencyMs: number | null;
  kbps: number;
  synthetic: boolean;
  error: string | null;
  frames: number;
}

const CAT_COLOR: Record<string, string> = {
  person: "#ff5468", vegetation: "#34d58a", structure: "#f2b33d", path: "#c08bff", sky: "#4c8dff",
  obstacle: "#ff8a3d", animal: "#ff8a3d", object: "#38d6ff",
};

function drawOverlay(ctx: CanvasRenderingContext2D, w: number, h: number, obs: Observation | null, nowS: number) {
  ctx.clearRect(0, 0, w, h);
  if (!obs) return;
  const age = nowS - obs.ts;
  ctx.lineWidth = Math.max(1.5, w / 640);
  ctx.font = `${Math.max(11, Math.round(w / 70))}px ui-monospace, monospace`;
  for (const d of obs.detections) {
    // Frame-spanning regions (sky, large ground cover) are listed in Mode B, not boxed here.
    if (d.category === "sky" || (d.area_fraction ?? 0) > 0.45) continue;
    const [x, y, bw, bh] = d.bbox;
    const color = CAT_COLOR[d.category] ?? "#38d6ff";
    ctx.strokeStyle = color;
    ctx.globalAlpha = age > 30 ? 0.35 : 0.9;
    ctx.strokeRect(x * w, y * h, bw * w, bh * h);
    const text = `${d.label} ${(d.confidence * 100).toFixed(0)}% · ${d.method}`;
    const tw = ctx.measureText(text).width + 8;
    ctx.fillStyle = "#000000b0";
    ctx.fillRect(x * w, Math.max(0, y * h - 16), tw, 16);
    ctx.fillStyle = color;
    ctx.fillText(text, x * w + 4, Math.max(12, y * h - 4));
  }
  ctx.globalAlpha = 1;
  const label = `AI OVERLAY · OpenCV detections · ${age.toFixed(0)}s old · image-relative only`;
  ctx.fillStyle = "#000000a0";
  ctx.fillRect(8, h - 24, ctx.measureText(label).width + 12, 18);
  ctx.fillStyle = "#38d6ff";
  ctx.fillText(label, 14, h - 11);
}

export function CameraPanel({ className }: { className?: string }) {
  const st = useTelemetry();
  const cam = st?.camera ?? null;
  const obs = st?.mapping?.vision?.latest ?? null;
  const toast = useToast();
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const overlayRef = useRef<HTMLCanvasElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const [transport, setTransport] = useState<"mjpeg" | "webrtc">("mjpeg");
  const [maxFps, setMaxFps] = useState(0);
  const [overlay, setOverlay] = useState(true);
  const [paused, setPaused] = useState(false);
  const [metrics, setMetrics] = useState<StreamMetrics>({ fps: 0, latencyMs: null, kbps: 0, synthetic: false, error: null, frames: 0 });
  const [info, setInfo] = useState<CameraStatus["info"] | null>(null);
  const [busy, setBusy] = useState(false);
  const [reconnectKey, setReconnectKey] = useState(0);
  const available = !!cam?.available;
  const webrtcAvailable = !!cam?.stream?.webrtc_enabled;

  useEffect(() => {
    if (!available) return;
    get<CameraStatus>("/api/camera/status").then((s) => setInfo(s.info ?? null)).catch(() => {});
  }, [available, cam?.width, cam?.height]);

  // ---------------------------------------------------------------- MJPEG
  useEffect(() => {
    if (!available || paused || transport !== "mjpeg") return;
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
        const c = canvasRef.current;
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
              pending = frames[frames.length - 1]; // drop stale frames: always show the newest
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
      setMetrics((m) => ({
        fps: counters.frames,
        latencyMs: counters.latN ? counters.latSum / counters.latN : null,
        kbps: (counters.bytes * 8) / 1000,
        synthetic: counters.synthetic,
        error: lastErr,
        frames: m.frames + counters.frames,
      }));
      counters.frames = counters.bytes = counters.latSum = counters.latN = 0;
    }, 1000);
    return () => {
      ac.abort();
      clearInterval(tick);
    };
  }, [available, paused, transport, maxFps, reconnectKey]);

  // --------------------------------------------------------------- WebRTC
  useEffect(() => {
    if (!available || paused || transport !== "webrtc" || !videoRef.current) return;
    let session: WhepSession | null = null;
    let closed = false;
    let lastFrames = 0;
    const video = videoRef.current;
    startWhep(video)
      .then((s) => {
        if (closed) s.close();
        else session = s;
      })
      .catch((e) => setMetrics((m) => ({ ...m, error: errorMessage(e) })));
    const tick = window.setInterval(async () => {
      if (!session) return;
      const q = video.getVideoPlaybackQuality?.();
      const frames = q ? q.totalVideoFrames : 0;
      const lat = await webrtcLatencyMs(session.pc).catch(() => null);
      setMetrics((m) => ({ ...m, fps: frames - lastFrames, latencyMs: lat, error: null, synthetic: false }));
      lastFrames = frames;
    }, 1000);
    return () => {
      closed = true;
      clearInterval(tick);
      session?.close();
    };
  }, [available, paused, transport]);

  // -------------------------------------------------------------- overlay
  useEffect(() => {
    const c = overlayRef.current;
    const base = canvasRef.current;
    if (!c) return;
    const w = base?.width || cam?.width || 1280;
    const h = base?.height || cam?.height || 720;
    c.width = w;
    c.height = h;
    const ctx = c.getContext("2d");
    if (ctx) drawOverlay(ctx, w, h, overlay ? obs : null, telemetry.clock.serverNow());
  }, [overlay, obs, cam?.width, cam?.height, metrics.frames]);

  // -------------------------------------------------------------- actions
  const act = useCallback(async (fn: () => Promise<unknown>, ok?: string) => {
    setBusy(true);
    try {
      await fn();
      if (ok) toast(ok, "ok");
    } catch (e) {
      toast(errorMessage(e), "error");
    } finally {
      setBusy(false);
    }
  }, [toast]);

  const snapshot = () => act(async () => {
    const s = await post<{ id: string }>("/api/camera/snapshot");
    toast(`Snapshot saved: ${s.id}`, "ok");
  });
  const toggleRec = () => act(() => post(cam?.recording.active ? "/api/camera/recording/stop" : "/api/camera/recording/start"),
    cam?.recording.active ? "Recording saved" : "Recording started");
  const setMode = (w: number, h: number, fps: number) => act(() => post("/api/camera/config", { width: w, height: h, fps }), `Stream set to ${w}×${h} @ ${fps} fps`);
  const fullscreen = () => {
    const el = wrapRef.current;
    if (!el) return;
    if (document.fullscreenElement) void document.exitFullscreen();
    else void el.requestFullscreen?.();
  };

  const modes = info?.modes ?? [];
  const curMode = modes.find((m) => m.width === cam?.width && m.height === cam?.height);
  const rec = cam?.recording;

  return (
    <Panel
      title="Live Camera"
      icon={<Camera size={14} />}
      className={className}
      badge={<StateBadge state={cam?.status.state} />}
      actions={
        <>
          {metrics.synthetic && <Tag tone="synth">SYNTHETIC FEED</Tag>}
          {rec?.active && (
            <Tag tone="crit">
              <Circle size={8} className="blink fill-current" /> REC {fmtDuration(rec.duration_s)}
            </Tag>
          )}
        </>
      }
      bodyClassName="!p-0 flex flex-col"
    >
      <div ref={wrapRef} className="relative flex min-h-[200px] flex-1 items-center justify-center overflow-hidden bg-black">
        {!available ? (
          <Empty>
            <div>
              <div className="mb-1 text-crit">Camera unavailable</div>
              <div className="max-w-md text-[11px]">{cam?.status.error ?? "No camera status yet"}</div>
            </div>
          </Empty>
        ) : transport === "mjpeg" ? (
          <>
            <canvas ref={canvasRef} className="max-h-full max-w-full object-contain" style={{ aspectRatio: `${cam?.width ?? 16}/${cam?.height ?? 9}` }} />
            <canvas ref={overlayRef} className="pointer-events-none absolute max-h-full max-w-full object-contain" style={{ aspectRatio: `${cam?.width ?? 16}/${cam?.height ?? 9}` }} />
          </>
        ) : (
          <video ref={videoRef} autoPlay playsInline muted className="max-h-full max-w-full" />
        )}
        {paused && available && <div className="absolute inset-0 flex items-center justify-center bg-black/70 text-muted">Stream paused</div>}
        {metrics.error && available && !paused && (
          <div className="absolute left-2 top-2 rounded bg-black/70 px-2 py-1 text-[11px] text-warn">
            {metrics.error} — reconnecting…
          </div>
        )}
        <div className="num absolute bottom-2 right-2 flex flex-col items-end gap-0.5 rounded bg-black/60 px-2 py-1 text-[11px]">
          <span>
            <span className="text-muted">RX</span> {metrics.fps} fps · <span className="text-muted">CAM</span> {fmt(cam?.measured_fps, 1)} fps
          </span>
          <span>
            <span className="text-muted">LAT</span>{" "}
            <span className={metrics.latencyMs !== null && metrics.latencyMs > 400 ? "text-warn" : ""}>{metrics.latencyMs === null ? "—" : `${metrics.latencyMs.toFixed(0)} ms`}</span>
            {transport === "mjpeg" ? " glass→browser" : " rx est."}
          </span>
          <span><span className="text-muted">BW</span> {(metrics.kbps / 1000).toFixed(2)} Mb/s</span>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-1.5 border-t border-line px-2 py-1.5">
        <button className="btn btn-sm" onClick={snapshot} disabled={!available || busy} title="Save the current frame">
          <Camera size={13} /> Snapshot
        </button>
        <button className={`btn btn-sm ${rec?.active ? "btn-danger" : ""}`} onClick={toggleRec} disabled={!available || busy}>
          {rec?.active ? <Square size={12} /> : <Video size={13} />} {rec?.active ? "Stop" : "Record"}
        </button>
        <button className="btn btn-sm" onClick={fullscreen} disabled={!available} title="Fullscreen">
          <Expand size={13} />
        </button>
        <button className={`btn btn-sm ${overlay ? "btn-primary" : ""}`} onClick={() => setOverlay((v) => !v)} title="AI observation overlay">
          <Layers size={13} /> Overlay
        </button>
        <button className="btn btn-sm" onClick={() => setPaused((p) => !p)} disabled={!available}>{paused ? "Resume" : "Pause"}</button>
        <button className="btn btn-sm" onClick={() => setReconnectKey((k) => k + 1)} title="Reconnect stream"><RefreshCw size={12} /></button>
        <div className="ml-auto flex flex-wrap items-center gap-1.5">
          {modes.length > 0 && (
            <select
              className="select !py-0.5"
              aria-label="Resolution"
              value={cam ? `${cam.width}x${cam.height}` : ""}
              disabled={busy || rec?.active}
              onChange={(e) => {
                const [w, h] = e.target.value.split("x").map(Number);
                const m = modes.find((x) => x.width === w && x.height === h);
                const fps = m?.fps.includes(cam?.target_fps ?? 30) ? (cam?.target_fps ?? 30) : (m?.fps.filter((f) => f <= 30).pop() ?? 30);
                void setMode(w, h, fps);
              }}
            >
              {modes.map((m) => <option key={`${m.width}x${m.height}`} value={`${m.width}x${m.height}`}>{m.width}×{m.height}</option>)}
            </select>
          )}
          {curMode && (
            <select className="select !py-0.5" aria-label="Frame rate" value={cam?.target_fps ?? 30} disabled={busy || rec?.active}
              onChange={(e) => void setMode(curMode.width, curMode.height, Number(e.target.value))}>
              {curMode.fps.map((f) => <option key={f} value={f}>{f} fps</option>)}
            </select>
          )}
          <select className="select !py-0.5" aria-label="Stream FPS cap" value={maxFps} onChange={(e) => setMaxFps(Number(e.target.value))} title="Limit browser stream rate to save Wi-Fi bandwidth">
            <option value={0}>Full rate</option>
            <option value={15}>≤15 fps</option>
            <option value={10}>≤10 fps</option>
            <option value={5}>≤5 fps</option>
          </select>
          {webrtcAvailable && (
            <select className="select !py-0.5" aria-label="Transport" value={transport} onChange={(e) => setTransport(e.target.value as "mjpeg" | "webrtc")}>
              <option value="mjpeg">MJPEG</option>
              <option value="webrtc">WebRTC (exp.)</option>
            </select>
          )}
          <span className="num text-[11px] text-muted">{cam?.width}×{cam?.height} · {fmtBytes(cam?.last_frame_bytes)}/frame</span>
        </div>
      </div>
    </Panel>
  );
}
