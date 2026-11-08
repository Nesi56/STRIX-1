// Types mirror the backend payloads (backend/app/services/*). All timestamps
// are UNIX seconds (server clock) unless suffixed with _ms.

export type DeviceState = "starting" | "ok" | "degraded" | "synthetic" | "disconnected" | "disabled" | "error";

export interface DeviceStatus {
  name: string;
  state: DeviceState;
  backend: string;
  synthetic: boolean;
  error: string | null;
  detail: Record<string, unknown>;
  last_update: number | null;
}

export interface Vec3 { x: number; y: number; z: number }

export interface ImuSample {
  ts: number;
  seq: number;
  accel: Vec3; // m/s²
  gyro: Vec3; // °/s
  temp_c: number | null;
  orientation: { roll: number; pitch: number; yaw_relative: number; accel_trust: number; accel_norm_g: number };
  rate_hz: number | null;
  saturated: boolean;
}

export interface HealthFinding { severity: "info" | "warning" | "critical"; code: string; message: string }

export interface EnvSample { ts: number; temperature_c: number; temperature_f: number; humidity_pct: number }

export interface EnvSummary {
  status: DeviceStatus;
  latest: EnvSample | null;
  age_s: number | null;
  stale: boolean;
  session: { temp_min: number | null; temp_max: number | null; hum_min: number | null; hum_max: number | null; since: number };
  reads: number;
  failures: number;
  interval_s: number;
  accuracy: { temperature_c: number; humidity_pct: number };
}

export interface ServoState {
  status: DeviceStatus;
  available: boolean;
  armed: boolean;
  target_angle: number | null;
  commanded_angle: number | null;
  moving: boolean;
  attached: boolean;
  limits: { min_angle: number; max_angle: number; center_angle: number; max_speed_dps: number; hardware_min: number; hardware_max: number };
  pulse_range_us: [number, number];
  gpio: number;
  pin?: number | null;
  estop_latched?: boolean;
  position_feedback: boolean;
  tilt_deg: number | null;
  up_is_positive: boolean;
  requested_angle?: number;
  clamped?: boolean;
}

export interface CameraMode { width: number; height: number; fps: number[] }

export interface CameraStatus {
  status: DeviceStatus;
  available: boolean;
  width: number | null;
  height: number | null;
  target_fps: number | null;
  measured_fps: number;
  frame_seq: number;
  last_frame_ts: number | null;
  last_frame_bytes: number | null;
  timestamp_method: string | null;
  recording: { active: boolean; file: string | null; started_at: number | null; duration_s: number | null; bytes: number | null; error: string | null };
  stream: { mjpeg_url: string; webrtc_enabled: boolean };
  info?: { model: string | null; sensor_mode: { size: number[]; fps: number; bit_depth: number } | null; modes: CameraMode[]; raw_sensor_modes: { size: number[]; fps: number; bit_depth: number }[] } | null;
  fps_note?: string;
}

export interface PowerInfo {
  throttled: Record<string, boolean | string> | null;
  pmic: { input_voltage_v: number | null; rail_power_w: number | null } | null;
  undervoltage_now: boolean | null;
  undervoltage_occurred: boolean | null;
  input_voltage_v: number | null;
  source: string | null;
  battery: { available: boolean; percent: number | null; reason: string };
}

export interface SystemInfo {
  ts: number;
  hostname: string;
  pi_model: string | null;
  is_raspberry_pi: boolean;
  cpu_percent: number;
  cpu_per_core: number[];
  cpu_count: number;
  cpu_freq_mhz: number | null;
  load_avg: number[];
  cpu_temp_c: number | null;
  memory: { total_mb: number; available_mb: number; used_percent: number; swap_used_percent: number };
  disk: { total_gb: number; free_gb: number; percent: number } | null;
  network: { rx_bps: number | null; tx_bps: number | null; wireless: Record<string, { link_quality: number; signal_dbm: number }> };
  power: PowerInfo;
  processes: Record<string, { pid: number; cpu_percent: number; rss_mb: number; name?: string; threads?: number }>;
  uptime_s: number;
  warnings: HealthFinding[];
}

export interface AIStats {
  latency_ms?: number; ttft_ms?: number | null; tokens_per_s?: number | null; prompt_tokens?: number | null;
  completion_tokens?: number | null; cost_usd?: number | null; tokens_estimated?: boolean; ts?: number;
}

export interface AIBrief {
  provider: string; label: string; enabled: boolean; state: string; detail: string | null; busy?: boolean; queued?: number;
  vision_capable: boolean; model_id?: string; last: AIStats | null; installed?: boolean;
}

export interface DetectedObject {
  label: string; confidence: number; bbox: [number, number, number, number];
  distance_m: number | null; distance_kind: "estimate" | "upper_bound" | "none";
  bearing_deg: number; elevation_deg: number; source: string;
}

export interface DetectionResult {
  seq: number; ts: number; frame_seq: number; frame_w: number; frame_h: number;
  detections: DetectedObject[]; synthetic: boolean; prompts: string[];
}

export interface DetectorState {
  status: DeviceStatus; engine: string; description: string; prompts: string[]; vocabulary_size: number;
  fps: number; infer_ms: number | null; hfov_deg: number; distance_method: string;
}

export interface PlanStep {
  skill: string; text: string; status: "pending" | "running" | "done" | "failed" | "skipped"; result?: string;
  target?: string; tilt?: number; delta?: number; seconds?: number;
}

export interface AgentState {
  status: "idle" | "running" | "done" | "stopped" | "error";
  node: "generator" | "observer" | "pilot" | "until" | "report" | "complete" | null;
  plan: PlanStep[]; task: string | null; run_id: string | null; planner: "qwen" | "rules" | null;
  step: number | null; error: string | null; watch?: boolean;
}

export type MemoryKind = "seen" | "remembered" | "hidden";

export interface MemoryObject {
  id: number; label: string; name: string;
  state: "visible" | "out_of_view" | "occluded" | "hidden";
  memory: MemoryKind;
  container: boolean; contained_by: number | null; contained_by_name: string | null; contains: number[];
  az: number; el: number; w_deg: number; h_deg: number;
  range_m: number | null; range_kind: string;
  xyz: [number, number, number];
  first_seen: number; last_seen: number; out_of_sight_s: number; hidden_for_s: number | null;
  confidence: number; projected_bbox: [number, number, number, number] | null; relocated_by_deg: number | null;
}

export interface MemoryEvent { ts: number; kind: string; text: string; track: number | null }

export interface DemoScene { scene: "outdoor" | "cups"; t?: number; cycle_s?: number; phase?: string; ball_hidden?: boolean }

export interface MemoryState {
  seq: number; ts: number; objects: MemoryObject[]; events: MemoryEvent[];
  counts: Record<MemoryKind, number>;
  camera: { az: number; el: number; half_h: number; half_v: number };
  frame: string; scene: DemoScene | null;
}

export interface LocalizationSource {
  id: string; name: string; kind: string; installed: boolean; running: boolean; validated: boolean;
  experimental: boolean; note: string; frame: string;
}

export interface VOState {
  enabled: boolean;
  error: string | null;
  experimental: boolean;
  validated: boolean;
  synthetic_input?: boolean;
  estimate?: {
    state: string; frames: number; accepted_steps: number; lost_events: number;
    quality: { features?: number; matches?: number; inliers?: number; inlier_ratio?: number; parallax_px?: number };
    path_length_units: number; running_s: number;
    trajectory: { ts: number; x: number; y: number; z: number }[];
    units: string; drift: string;
  } | null;
}

export interface MappingState {
  position: { available: boolean; message: string; source: string | null };
  sources: LocalizationSource[];
  visual_odometry: VOState;
  imu_position: string;
}

export interface RecordingStatus {
  active: boolean;
  session: { id: number; name: string; started_at: number; synthetic: boolean; duration_s: number; rows: number } | null;
  sample_hz: number;
  db_bytes: number;
  dropped_writes: number;
  retention_days: number;
}

export interface TelemetryEvent { ts: number; level: string; source: string; message: string }

export interface TelemetryState {
  ts: number;
  seq: number;
  demo_mode: boolean;
  imu: { status: DeviceStatus; latest: ImuSample | null };
  imu_health: HealthFinding[];
  imu_calibration: { state: string; yaw_drift_bound_deg: number | null; calibrated_at: number | null } | null;
  environment: EnvSummary | null;
  servo: ServoState | null;
  camera: CameraStatus | null;
  system: SystemInfo | null;
  ai: { qwen: AIBrief } | null;
  mapping: MappingState | null;
  detections?: DetectionResult | null;
  detector?: DetectorState;
  agent?: AgentState;
  memory?: MemoryState;
  recording: RecordingStatus | null;
}

export interface Session {
  id: number; name: string; notes: string | null; started_at: number; ended_at: number | null; sample_hz: number;
  synthetic: boolean; samples: number; duration_s: number; active: boolean; metadata: Record<string, unknown>;
}

export interface TelemetryRow {
  ts: number; ax: number | null; ay: number | null; az: number | null; gx: number | null; gy: number | null; gz: number | null;
  imu_temp: number | null; roll: number | null; pitch: number | null; yaw_rel: number | null; env_temp: number | null;
  humidity: number | null; env_ts: number | null; servo_angle: number | null; cpu_percent: number | null;
  mem_percent: number | null; cpu_temp: number | null; camera_fps: number | null; status: Record<string, string>;
}

export interface MediaFile { name: string; bytes: number; modified: number; synthetic: boolean }

