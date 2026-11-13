import { afterEach, describe, expect, it } from "vitest";
import { droneUrl, parseRoute, setDrone } from "../services/drone";

describe("drone routing", () => {
  afterEach(() => setDrone(null));

  it("leaves paths alone in direct mode", () => {
    expect(droneUrl("/api/servo")).toBe("/api/servo");
    expect(droneUrl("/ws/telemetry")).toBe("/ws/telemetry");
  });

  it("prefixes drone routes in hub mode, never login or hub routes", () => {
    setDrone("alpha");
    expect(droneUrl("/api/servo/nudge")).toBe("/d/alpha/api/servo/nudge");
    expect(droneUrl("/ws/telemetry")).toBe("/d/alpha/ws/telemetry");
    expect(droneUrl("/api/auth/login")).toBe("/api/auth/login");
    expect(droneUrl("/api/hub/fleet")).toBe("/api/hub/fleet");
    expect(droneUrl("/api/camera/frame.jpg", "/d/bravo")).toBe("/d/bravo/api/camera/frame.jpg");
  });

  it("parses routes", () => {
    expect(parseRoute("#/d/bravo-2")).toEqual({ view: "drone", id: "bravo-2" });
    expect(parseRoute("#/")).toEqual({ view: "fleet" });
    expect(parseRoute("#/d/../x")).toEqual({ view: "fleet" });
    expect(parseRoute("")).toEqual({ view: "fleet" });
  });
});
