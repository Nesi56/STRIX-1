// Keyboard shortcuts for the camera servo (there is no separate control panel):
// W/↑ tilt up · S/↓ tilt down · C level · E servo on/off · Space EMERGENCY STOP · Esc stop Qwen's task.
import { useEffect, useRef } from "react";
import { errorMessage, post } from "../services/api";
import { emergencyStop } from "../components/console/TopBar";
import { useToast } from "../components/common/toast";
import { telemetry } from "./useTelemetry";

export const TILT_STEP = 5;

export async function servoAction(id: string, toast: ReturnType<typeof useToast>, armedHint?: { v: boolean; t: number } | null) {
  try {
    if (id === "estop") return void (await emergencyStop(toast));
    if (id === "stop") {
      const r = await post<{ cancelled: boolean }>("/api/agent/stop");
      if (r.cancelled) toast("Stopped Qwen's task", "warn");
      return;
    }
    if (id === "toggle") {
      const sv = telemetry.snap.state?.servo;
      const armed = armedHint && Date.now() - armedHint.t < 1500 ? armedHint.v : !!sv?.armed;
      await post(armed ? "/api/servo/disarm" : "/api/servo/arm");
      toast(armed ? "Camera servo off" : "Camera servo on", armed ? "warn" : "ok");
      return !armed;
    }
    // Like the original servo panel: tilting needs "Enable control" (E) first; the backend says so if not.
    if (id === "center") await post("/api/servo/center");
    if (id === "up") await post("/api/servo/nudge", { up_deg: TILT_STEP });
    if (id === "down") await post("/api/servo/nudge", { up_deg: -TILT_STEP });
  } catch (e) {
    toast(errorMessage(e), "error");
  }
}

export function useServoKeys() {
  const toast = useToast();
  const last = useRef(0);
  const armedHint = useRef<{ v: boolean; t: number } | null>(null);
  useEffect(() => {
    const map: Record<string, string> = { w: "up", arrowup: "up", s: "down", arrowdown: "down", c: "center", e: "toggle", " ": "estop", escape: "stop" };
    const onDown = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable)) return;
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const id = map[e.key.toLowerCase()];
      if (!id) return;
      e.preventDefault();
      const now = Date.now();
      if (e.repeat && (id === "toggle" || id === "estop" || id === "stop" || now - last.current < 120)) return;
      last.current = now;
      void servoAction(id, toast, armedHint.current).then((v) => {
        if (typeof v === "boolean") armedHint.current = { v, t: Date.now() };
      });
    };
    window.addEventListener("keydown", onDown);
    return () => window.removeEventListener("keydown", onDown);
  }, [toast]);
}
