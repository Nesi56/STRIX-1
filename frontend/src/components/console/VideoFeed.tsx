import { Camera, Circle, Expand } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useMjpeg } from "../../hooks/useMjpeg";
import { telemetry, useTelemetry } from "../../hooks/useTelemetry";
import { MEM_COLOR, stateText } from "../../lib/memory";
import { errorMessage, post } from "../../services/api";
import { fmtDuration } from "../../lib/format";
import type { DetectedObject, MemoryState } from "../../types/telemetry";
import { useToast } from "../common/toast";

const GREEN = "#39ff7a";

export function boxLabel(d: DetectedObject): string {
  const r = d.distance_m == null ? "" : d.distance_kind === "upper_bound" ? ` · ≤${d.distance_m.toFixed(1)} m` : ` · ~${d.distance_m.toFixed(1)} m`;
  return `${d.label} ${d.confidence.toFixed(2)}${r}`;
}

/** Remembered / hidden objects projected into the current view: dashed boxes ("see-through" memory). */
function drawMemory(ctx: CanvasRenderingContext2D, w: number, h: number, mem: MemoryState | null) {
  if (!mem) return;
  const lw = Math.max(2, w / 600);
  const fs = Math.max(11, Math.round(w / 74));
  const now = telemetry.clock.serverNow();
  ctx.font = `600 ${fs}px ui-monospace, monospace`;
  ctx.setLineDash([lw * 4, lw * 3]);
  for (const o of mem.objects) {
    if (o.memory === "seen" || !o.projected_bbox) continue;
    const [x, y, bw, bh] = o.projected_bbox;
    const color = MEM_COLOR[o.memory];
    ctx.strokeStyle = color;
    ctx.lineWidth = lw;
    ctx.strokeRect(x * w, y * h, bw * w, bh * h);
    const text = `${o.name} · ${stateText(o, mem, now)}`;
    const tw = ctx.measureText(text).width + 10;
    const ty = Math.min(h - fs - 6, (y + bh) * h + 3);
    ctx.fillStyle = "rgba(0,0,0,0.72)";
    ctx.fillRect(x * w, ty, tw, fs + 6);
    ctx.fillStyle = color;
    ctx.fillText(text, x * w + 5, ty + fs);
  }
  ctx.setLineDash([]);
}

function drawBoxes(ctx: CanvasRenderingContext2D, w: number, h: number, dets: DetectedObject[], stale: boolean) {
  ctx.clearRect(0, 0, w, h);
  const lw = Math.max(2, w / 520);
  const fs = Math.max(12, Math.round(w / 64));
  ctx.font = `600 ${fs}px ui-monospace, monospace`;
  ctx.globalAlpha = stale ? 0.4 : 1;
  const placed: [number, number, number, number][] = [];
  const free = (r: [number, number, number, number]) =>
    !placed.some((q) => r[0] < q[0] + q[2] && q[0] < r[0] + r[2] && r[1] < q[1] + q[3] && q[1] < r[1] + r[3]);
  for (const d of [...dets].sort((a, b) => a.bbox[0] - b.bbox[0])) {
    const [x, y, bw, bh] = d.bbox;
    ctx.strokeStyle = GREEN;
    ctx.lineWidth = lw;
    ctx.strokeRect(x * w, y * h, bw * w, bh * h);
    const text = boxLabel(d);
    const tw = ctx.measureText(text).width + 10;
    const lh = fs + 6;
    // stack labels of neighbouring boxes instead of overlapping them
    const tries = [0, 1, 2, 3].map((k) => Math.max(0, y * h - lh * (k + 1) - k * 2));
    const ty = tries.find((t) => free([x * w, t, tw, lh])) ?? tries[0];
    placed.push([x * w, ty, tw, lh]);
    if (ty < y * h - lh - 1) {
      ctx.beginPath();
      ctx.moveTo(x * w + 4, ty + lh);
      ctx.lineTo(x * w + 4, y * h);
      ctx.stroke();
    }
    ctx.fillStyle = GREEN;
    ctx.fillRect(x * w - lw / 2, ty, tw, lh);
    ctx.fillStyle = "#04150b";
    ctx.fillText(text, x * w + 4, ty + fs);
    ctx.beginPath();
    ctx.arc((x + bw / 2) * w, (y + bh / 2) * h, lw * 1.6, 0, Math.PI * 2);
    ctx.fillStyle = GREEN;
    ctx.fill();
  }
  // camera axis crosshair
  ctx.globalAlpha = 0.5;
  ctx.strokeStyle = "#ffffff";
  ctx.lineWidth = 1;
  const c = Math.max(10, w / 80);
  ctx.beginPath();
  ctx.moveTo(w / 2 - c, h / 2); ctx.lineTo(w / 2 + c, h / 2);
  ctx.moveTo(w / 2, h / 2 - c); ctx.lineTo(w / 2, h / 2 + c);
  ctx.stroke();
  ctx.globalAlpha = 1;
}

export function VideoFeed() {
  const st = useTelemetry();
  const cam = st?.camera ?? null;
  const det = st?.detections ?? null;
  const mem = st?.memory ?? null;
  const toast = useToast();
  const wrap = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const overlay = useRef<HTMLCanvasElement>(null);
  const [busy, setBusy] = useState(false);
  const available = !!cam?.available;
  const m = useMjpeg(canvas, available, 0, 0);

  useEffect(() => {
    const o = overlay.current;
    if (!o) return;
    const w = canvas.current?.width || cam?.width || 1280;
    const h = canvas.current?.height || cam?.height || 720;
    if (o.width !== w || o.height !== h) {
      o.width = w;
      o.height = h;
    }
    const ctx = o.getContext("2d");
    if (ctx) {
      drawBoxes(ctx, w, h, det?.detections ?? [], !!det && Date.now() / 1000 - det.ts > 3);
      drawMemory(ctx, w, h, mem);
    }
  }, [det, mem, cam?.width, cam?.height, m.frames]);

  const rec = cam?.recording;
  const aspect = `${cam?.width ?? 16}/${cam?.height ?? 9}`;
  const snapshot = async () => {
    setBusy(true);
    try {
      toast(`Snapshot saved: ${(await post<{ id: string }>("/api/camera/snapshot")).id}`, "ok");
    } catch (e) {
      toast(errorMessage(e), "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section ref={wrap} className="panel relative flex h-full min-h-[240px] items-center justify-center overflow-hidden !rounded-[4px] bg-black">
      {!available ? (
        <div className="p-6 text-center">
          <div className="text-crit">CAMERA UNAVAILABLE</div>
          <div className="mt-1 max-w-md text-[11px] text-muted">{cam?.status.error ?? "waiting for camera status…"}</div>
        </div>
      ) : (
        <>
          <canvas ref={canvas} className="max-h-full max-w-full" style={{ aspectRatio: aspect }} />
          <canvas ref={overlay} className="pointer-events-none absolute max-h-full max-w-full" style={{ aspectRatio: aspect }} />
        </>
      )}
      <div className="absolute left-2 top-2 flex items-center gap-2 text-[10.5px]">
        <span className={`rounded-[3px] px-1.5 py-0.5 ${m.synthetic ? "bg-synth/20 text-synth" : "bg-accent/15 text-accent"}`}>
          ● {m.synthetic ? "SYNTHETIC FEED" : "LIVE"}
        </span>
        {rec?.active && <span className="rounded-[3px] bg-crit/20 px-1.5 py-0.5 text-crit"><Circle size={8} className="blink inline fill-current" /> REC {fmtDuration(rec.duration_s)}</span>}
      </div>
      <div className="absolute right-2 top-2 hidden rounded-[3px] bg-black/60 px-2 py-0.5 text-[10.5px] sm:block" title="frames per second received · camera-to-screen delay">
        {m.fps} fps · {m.latencyMs == null ? "—" : `${m.latencyMs.toFixed(0)} ms`}
      </div>
      {m.error && available && <div className="absolute left-2 top-9 rounded bg-black/70 px-2 py-1 text-[11px] text-warn">{m.error} — reconnecting…</div>}
      <div className="absolute bottom-2 right-2 flex gap-1">
        <button className="btn btn-sm !bg-black/70" title="Snapshot" disabled={!available || busy} onClick={() => void snapshot()}><Camera size={12} /></button>
        <button className="btn btn-sm !bg-black/70" title="Fullscreen"
          onClick={() => (document.fullscreenElement ? void document.exitFullscreen() : void wrap.current?.requestFullscreen?.())}><Expand size={12} /></button>
      </div>
    </section>
  );
}

