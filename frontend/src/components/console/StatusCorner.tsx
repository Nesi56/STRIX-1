import type { ReactNode } from "react";
import { useTelemetrySnapshot } from "../../hooks/useTelemetry";
import { signed } from "../../lib/format";

type Tone = "ok" | "warn" | "crit" | "synth" | "dim";
const DOT: Record<Tone, string> = { ok: "bg-accent shadow-[0_0_6px_#6fd3ff]", warn: "bg-warn", crit: "bg-crit", synth: "bg-synth", dim: "bg-dim" };
const TXT: Record<Tone, string> = { ok: "text-text", warn: "text-warn", crit: "text-crit", synth: "text-synth", dim: "text-muted" };

function Row({ label, tone, value, hint, title }: { label: string; tone: Tone; value: ReactNode; hint?: string | null; title?: string }) {
  return (
    <div title={title ?? hint ?? undefined} className="border-b border-line/70 py-[3px] last:border-0">
      <div className="flex items-center gap-1.5">
        <span className={`h-2 w-2 shrink-0 rounded-full ${DOT[tone]}`} />
        <span className="w-[62px] shrink-0 text-[10px] tracking-[0.1em] text-muted">{label}</span>
        <span className={`min-w-0 flex-1 truncate text-right text-[11px] ${TXT[tone]}`}>{value}</span>
      </div>
      {hint && <div className={`truncate pl-3.5 text-[9.5px] ${tone === "crit" ? "text-crit/80" : "text-dim"}`}>{hint}</div>}
    </div>
  );
}

const devTone = (s?: string): Tone =>
  s === "ok" ? "ok" : s === "synthetic" ? "synth" : s === "degraded" || s === "starting" ? "warn" : s === "disabled" ? "dim" : "crit";

/** Corner status: every sensor, the servo, Qwen and power at a glance — with the fix when something is red. */
export function StatusCorner() {
  const snap = useTelemetrySnapshot();
  const st = snap.state;
  const cam = st?.camera;
  const det = st?.detector;
  const q = st?.ai?.qwen;
  const imu = st?.imu;
  const env = st?.environment;
  const sv = st?.servo;
  const sys = st?.system;
  const pw = sys?.power;
  const mem = st?.memory;

  const qTone: Tone = q?.state === "ready" ? "ok" : q?.state === "loading" || (q?.installed && q?.state !== "error") ? "warn" : "crit";
  const qValue = q?.state === "ready" ? (q.vision_capable ? "ready · sees images" : "ready")
    : q?.state === "loading" ? "starting…" : q?.installed && q?.state !== "error" ? "starting…" : q?.state === "error" ? "error" : "not installed";
  const qHint = q?.state === "ready" || q?.state === "loading" ? null
    : q?.installed ? (q?.state === "error" ? q.detail : null) : "run scripts/setup_ai.sh once";
  const detTone: Tone = det?.engine === "hog" ? "warn" : devTone(det?.status.state);
  const pin = sv ? `GPIO${sv.gpio}${sv.pin ? `/pin ${sv.pin}` : ""}` : "";
  const svTone: Tone = !sv?.available ? "crit" : sv.estop_latched ? "crit" : sv.armed ? "ok" : "dim";
  const svValue = !sv ? "—" : !sv.available ? "not working" : sv.estop_latched ? "E-STOP" : `${sv.armed ? "on" : "off"} · ${pin}`;
  const battTone: Tone = pw?.undervoltage_now ? "crit" : pw?.undervoltage_occurred ? "warn" : "ok";

  return (
    <section className="panel shrink-0 px-2.5 py-1.5">
      <div className="mb-0.5 flex items-center justify-between">
        <span className="text-[10.5px] font-semibold tracking-[0.14em] text-text">STATUS</span>
        <span className={`text-[10px] ${snap.conn === "open" ? "text-accent" : "text-crit"}`}>{snap.conn === "open" ? "connected" : snap.conn}</span>
      </div>
      <Row label="CAMERA" tone={devTone(cam?.status.state)} value={cam?.available ? `${(cam.measured_fps ?? 0).toFixed(0)} fps` : "offline"}
        hint={cam && !cam.available ? cam.status.error : null} />
      <Row label="YOLO" tone={detTone} value={det ? (det.engine === "hog" ? "basic (people only)" : `${det.fps.toFixed(1)} fps`) : "—"}
        hint={det?.engine === "hog" ? "run scripts/setup_ai.sh" : det?.slowed ? `slowed to cool down (${det.slowed})` : det?.status.error} />
      <Row label="QWEN" tone={qTone} value={qValue} hint={qHint} title={q?.detail ?? undefined} />
      <Row label="MPU6050" tone={devTone(imu?.status.state)}
        value={imu?.latest ? `roll ${signed(imu.latest.orientation.roll, 0)}° pitch ${signed(imu.latest.orientation.pitch, 0)}°` : imu?.status.state ?? "—"}
        hint={imu?.latest ? null : imu?.status.error} />
      <Row label="DHT11" tone={devTone(env?.status.state)}
        value={env?.latest ? `${env.latest.temperature_c.toFixed(1)} °C · ${env.latest.humidity_pct.toFixed(0)} %` : env?.status.state ?? "—"}
        hint={env?.latest ? null : env?.status.error} />
      <Row label="SERVO" tone={svTone} value={svValue} hint={!sv?.available ? sv?.status.error : null}
        title={sv?.available ? "SG90 camera tilt servo. It has no sensor, so the Pi can't detect it — tap Servo on under the video and watch it move." : undefined} />
      <Row label="MEMORY" tone={mem ? (mem.counts.hidden ? "warn" : "ok") : "dim"}
        value={mem ? `${mem.counts.seen} seen · ${mem.counts.remembered + mem.counts.hidden} remembered` : "—"} />
      <Row label="CPU" tone={sys?.cpu_temp_c == null ? "dim" : sys.cpu_temp_c >= 80 ? "crit" : sys.cpu_temp_c >= 70 ? "warn" : "ok"}
        value={sys?.cpu_temp_c == null ? "—" : `${sys.cpu_temp_c.toFixed(0)} °C`}
        hint={sys?.cpu_temp_c != null && sys.cpu_temp_c >= 75 ? "hot: YOLO and Qwen's comments are slowed" : null}
        title="The Pi 5 throttles at 85 °C. An Active Cooler is strongly recommended when running AI." />
      <Row label="POWER" tone={battTone} value={pw?.undervoltage_now ? "LOW VOLTAGE" : pw?.undervoltage_occurred ? "dipped low earlier" : "OK"}
        title={pw?.battery?.reason ?? "The USB power bank doesn't report its charge to the Pi, so battery % can't be shown."} />
    </section>
  );
}
