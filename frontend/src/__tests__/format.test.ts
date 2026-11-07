import { describe, expect, it } from "vitest";
import { cToF, fmt, fmtBytes, fmtDuration, signed } from "../lib/format";
import { Ring } from "../lib/ring";

describe("format", () => {
  it("formats numbers and missing values", () => {
    expect(fmt(1.234, 1, "°")).toBe("1.2°");
    expect(fmt(null)).toBe("—");
    expect(fmt(Number.NaN)).toBe("—");
    expect(signed(2, 1)).toBe("+2.0");
    expect(signed(-2, 1)).toBe("-2.0");
  });
  it("converts units", () => {
    expect(cToF(0)).toBe(32);
    expect(cToF(100)).toBe(212);
    expect(fmtBytes(1500)).toBe("1.5 KB");
    expect(fmtDuration(3725)).toBe("1:02:05");
    expect(fmtDuration(65)).toBe("1:05");
  });
});

describe("Ring", () => {
  it("keeps the newest items up to capacity", () => {
    const r = new Ring<number>(3);
    [1, 2, 3, 4, 5].forEach((v) => r.push(v));
    expect(r.toArray()).toEqual([3, 4, 5]);
    expect(r.last()).toBe(5);
    expect(r.length).toBe(3);
  });
});
