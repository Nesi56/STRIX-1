import type { ReactNode } from "react";
import { useTelemetrySnapshot } from "../../hooks/useTelemetry";
import { fmtDuration, signed } from "../../lib/format";

type Tone = "ok" | "warn" | "crit" | "synth" | "dim";
const DOT: Record<Tone, string> = { ok: "bg-accent shadow-[0_0_6px_#3ddc84]", warn: "bg-warn", crit: "bg-crit", synth: "bg-synth", dim: "bg-dim" };
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

/** Corner status: every sensor, the servo and power at a glance — with the reason when something is red. */
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

  const qTone: Tone = q?.state === "ready" ? "ok" : q?.state === "loading" ? "warn" : "crit";
  const detTone: Tone = det?.engine === "hog" ? "warn" : devTone(det?.status.state);
  const battTone: Tone = pw?.undervoltage_now ? "crit" : pw?.undervoltage_occurred ? "warn" : pw?.input_voltage_v != null ? "ok" : "dim";

  return (
    <section className="panel shrink-0 px-2.5 py-1.5">
      <div className="mb-0.5 flex items-center justify-between">
        <span className="text-[10.5px] font-semibold tracking-[0.14em] text-text">SYSTEM STATUS</span>
        <span className={`text-[10px] ${snap.conn === "open" ? "text-accent" : "text-crit"}`}>
          link {snap.conn === "open" ? `${snap.rttMs?.toFixed(0) ?? "…"} ms` : snap.conn}
        </span>
      </div>
      <Row label="CAMERA" tone={devTone(cam?.status.state)}
        value={cam?.available ? `${(cam.measured_fps ?? 0).toFixed(0)} fps · ${cam.width}×${cam.height}` : "offline"}
        hint={cam && !cam.available ? cam.status.error : null} />
      <Row label="YOLO" tone={detTone}
        value={det ? (det.engine === "hog" ? "HOG fallback (person only)" : `${det.engine === "synthetic" ? "synthetic" : det.status.backend.replace("onnx:", "")} · ${det.fps.toFixed(1)} fps`) : "—"}
        hint={det?.engine === "hog" ? "install YOLO: scripts/get_detector_model.sh" : det?.status.error} />
      <Row label="QWEN" tone={qTone}
        value={q?.state === "ready" ? `ready · ${q.vision_capable ? "vision" : "text only"}` : q?.state === "loading" ? "loading model…" : "not running"}
        hint={q?.state === "ready" ? (q.vision_capable ? null : "for camera vision run scripts/setup_ai.sh") : q?.state === "loading" ? null : "on the Pi run: scripts/setup_ai.sh"} />
      <Row label="MPU6050" tone={devTone(imu?.status.state)}
        value={imu?.latest ? `roll ${signed(imu.latest.orientation.roll, 0)}° pitch ${signed(imu.latest.orientation.pitch, 0)}°` : imu?.status.state ?? "—"}
        hint={imu?.status.error} />
      <Row label="DHT11" tone={devTone(env?.status.state)}
        value={env?.latest ? `${env.latest.temperature_c.toFixed(1)} °C · ${env.latest.humidity_pct.toFixed(0)} %RH` : env?.status.state ?? "—"}
        hint={env?.status.error} />
      <Row label="SERVO" tone={!sv?.available ? "crit" : sv.armed ? "ok" : "dim"}
        value={sv?.available ? `${sv.armed ? "armed" : "safe (press E)"} · tilt ${signed(sv.tilt_deg, 0)}°` : "unavailable"}
        hint={!sv?.available ? sv?.status.error : null} />
      <Row label="BATTERY" tone={battTone}
        value={pw?.undervoltage_now ? "UNDER-VOLTAGE" : `no fuel gauge${pw?.input_voltage_v != null ? ` · ${pw.input_voltage_v.toFixed(2)} V in` : ""}`}
        hint={`running ${fmtDuration(sys?.uptime_s)}${sys?.cpu_temp_c != null ? ` · CPU ${sys.cpu_temp_c.toFixed(0)} °C` : ""}`}
        title={pw?.battery?.reason ?? "The USB power bank does not report its charge to the Pi, so % is not shown."} />
    </section>
  );
}
