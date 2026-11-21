import { describe, expect, it } from "vitest";
import { centroid, distance, niceExtent, toLocal } from "../lib/geo";

describe("geo", () => {
  const home = { lat: 47.3979712, lon: 8.5461639 };

  it("projects east/north in metres", () => {
    const p = toLocal({ lat: home.lat + 0.0001, lon: home.lon + 0.0001 }, home);
    expect(p.n).toBeCloseTo(11.06, 1);
    expect(p.e).toBeCloseTo(7.53, 1); // cos(47.4°) shrinks longitude
    expect(distance(home, { lat: home.lat, lon: home.lon + 0.001 })).toBeCloseTo(75.3, 0);
  });

  it("centres and picks round extents", () => {
    expect(centroid([{ lat: 1, lon: 2 }, { lat: 3, lon: 4 }])).toEqual({ lat: 2, lon: 3 });
    expect(niceExtent(3)).toBe(20);
    expect(niceExtent(30)).toBe(50);
    expect(niceExtent(70)).toBe(100);
    expect(niceExtent(900)).toBe(2000);
  });
});
