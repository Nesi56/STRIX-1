import { describe, expect, it } from "vitest";
import { dir, displayScale, fmtAgo, outOfSight, sizeM, stateText, toScene } from "../lib/memory";
import type { MemoryObject, MemoryState } from "../types/telemetry";

const obj = (over: Partial<MemoryObject>): MemoryObject => ({
  id: 4, label: "sports ball", name: "sports ball #4", state: "hidden", memory: "hidden", container: false,
  contained_by: 2, contained_by_name: "cup #2", contains: [], az: -13, el: -25, w_deg: 4.6, h_deg: 4.6,
  range_m: 0.75, range_kind: "estimate", xyz: [0, 0, 1], first_seen: 0, last_seen: 90, out_of_sight_s: 10,
  hidden_for_s: 10, confidence: 0.86, projected_bbox: null, relocated_by_deg: null, ...over,
});
const mem = (objects: MemoryObject[]): MemoryState => ({
  seq: 1, ts: 100, objects, events: [], counts: { seen: 0, remembered: 0, hidden: 1 },
  camera: { az: 0, el: 0, half_h: 51, half_v: 35 }, frame: "", scene: null,
});

describe("object memory helpers", () => {
  it("advances out-of-sight timers on the server clock", () => {
    const o = obj({});
    expect(outOfSight(o, mem([o]), 103.5)).toBeCloseTo(13.5);
    expect(outOfSight(obj({ memory: "seen", state: "visible" }), mem([o]), 103.5)).toBe(0);
    expect(stateText(o, mem([o]), 102)).toBe("HIDDEN under cup #2 · 12 s");
    expect(stateText(obj({ memory: "remembered", state: "out_of_view" }), mem([o]), 100)).toContain("out of view");
    expect(fmtAgo(4.25)).toBe("4.3 s");
    expect(fmtAgo(125)).toBe("2 min 5 s");
  });

  it("maps the memory frame to three.js and compresses far distances", () => {
    expect(toScene([0.1, -0.2, 0.7]).pos).toEqual([0.1, -0.2, -0.7]);
    expect(displayScale(1)).toBe(1);
    const far = toScene([0, 0, 10]);
    expect(far.pos[2]).toBeCloseTo(-(1.5 + 8.5 * 0.4));
    const d = dir(90, 0);
    expect(d[0]).toBeCloseTo(1);
    expect(d[2]).toBeCloseTo(0);
    expect(dir(0, 0)[2]).toBeCloseTo(-1);
  });

  it("derives a physical size from angular size and range", () => {
    const s = sizeM(obj({ w_deg: 7.6, h_deg: 7.6, range_m: 0.75 }));
    expect(s.h).toBeCloseTo(0.0996, 3);
  });
});
