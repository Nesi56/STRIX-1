// Shapes returned by the STRIX-1 hub (GET /api/hub/fleet). Every reading is
// either a real value from the drone or null; the hub never fills gaps.

export interface FleetDrone {
  id: string;
  name: string;
  color: string | null;
  online: boolean;
  error: string | null;
  version: string | null;
  connected_since: number | null;
  last_seen_s: number | null;
  demo_mode: boolean | null;
  attitude: { roll: number | null; pitch: number | null; yaw: number | null; state: string | null };
  environment: { temperature_c: number | null; humidity_pct: number | null; stale: boolean | null };
  servo: { available: boolean | null; armed: boolean | null; tilt_deg: number | null; estop_latched: boolean | null; stabilized?: boolean | null };
  system: { cpu_temp_c: number | null; cpu_percent: number | null };
  camera_fps: number | null;
  qwen: string | null;
  agent: { status: string | null; task: string | null };
  recording: boolean | null;
  objects: { label: string; count: number }[];
  memory: { remembered: number; hidden: number };
  /** Only present when the drone has a real battery telemetry source (a flight controller, real or simulated). */
  battery: { percent: number; source: string | null; synthetic: boolean | null } | null;
  /** Flight controller over MAVLink (read-only). Null when the drone has none. */
  flight: {
    connected: boolean | null; label: string | null; autopilot: string | null; armed: boolean | null; mode: string | null;
    landed: string | null; heading: number | null; groundspeed_ms: number | null; climb_ms: number | null; synthetic: boolean | null;
  } | null;
  /** Only with a 3D GPS fix. Never estimated. */
  position: {
    lat: number; lon: number; alt_rel_m: number | null; fix: string | null; satellites: number | null;
    synthetic: boolean | null; source: string | null;
  } | null;
  sim: { kind: string; label: string | null; model: string | null } | null;
}

export interface FleetResponse {
  version: string;
  drones: FleetDrone[];
  qwen: { state: string; relay: string | null } | null;
}
