import { describe, expect, it } from "vitest";
import { ClockSync, computeSample } from "../services/clock";

describe("clock sync", () => {
  it("computes NTP-style offset", () => {
    // client sends at 1000, server (ahead by 500 ms) stamps 1520, client receives at 1040 → rtt 40, offset 500
    const s = computeSample(1000, 1520, 1040);
    expect(s.rttMs).toBe(40);
    expect(s.offsetMs).toBe(500);
  });

  it("prefers the minimum-RTT sample", () => {
    const c = new ClockSync(5);
    c.add(computeSample(0, 600, 200)); // rtt 200 → offset 500
    c.add(computeSample(1000, 1505, 1010)); // rtt 10 → offset 500
    c.add(computeSample(2000, 2900, 2600)); // rtt 600, skewed estimate 600
    expect(c.best?.rttMs).toBe(10);
    expect(c.offsetMs).toBe(500);
    expect(c.serverNow(1000)).toBeCloseTo(1.5, 6);
  });

  it("ignores invalid samples and keeps a window", () => {
    const c = new ClockSync(2);
    c.add({ rttMs: -5, offsetMs: 0, at: 0 });
    expect(c.best).toBeNull();
    c.add(computeSample(0, 0, 50));
    c.add(computeSample(0, 0, 60));
    c.add(computeSample(0, 0, 70));
    expect(c.best?.rttMs).toBe(60);
  });
});
