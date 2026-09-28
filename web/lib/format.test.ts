import { describe, expect, it } from "vitest";

import { digitsForRadius, formatCoord, formatDistance, formatRatio } from "./format";
import { targetSize } from "./prepare-image";

describe("format", () => {
  it("shows only the decimals the uncertainty supports", () => {
    expect(digitsForRadius(15)).toBe(5);
    expect(digitsForRadius(240_000)).toBe(1);
  });

  it("formats coordinates with hemispheres", () => {
    expect(formatCoord(-33.9249, 18.4241, 2)).toBe("33.92° S, 18.42° E");
  });

  it("formats distances and likelihood ratios", () => {
    expect(formatDistance(850)).toBe("850 m");
    expect(formatDistance(2500)).toBe("2.5 km");
    expect(formatRatio(32.4)).toBe("×32");
    expect(formatRatio(0.25)).toBe("÷4.0");
  });

  it("downscales to the long edge without upscaling", () => {
    expect(targetSize(4032, 3024)).toEqual({ width: 1024, height: 768 });
    expect(targetSize(800, 600)).toEqual({ width: 800, height: 600 });
  });
});
