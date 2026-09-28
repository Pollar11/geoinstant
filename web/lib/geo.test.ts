import { describe, expect, it } from "vitest";

import { circlePolygon, destination, haversineKm, zoomForRadius } from "./geo";

describe("geo", () => {
  it("haversine matches a known distance (Paris–London ≈ 344 km)", () => {
    expect(haversineKm(48.8566, 2.3522, 51.5074, -0.1278)).toBeCloseTo(343.5, 0);
  });

  it("destination points lie at the requested distance", () => {
    for (const bearing of [0, 90, 200]) {
      const [lon, lat] = destination(35, 139, 250, bearing);
      expect(haversineKm(35, 139, lat, lon)).toBeCloseTo(250, 3);
    }
  });

  it("circle crosses the antimeridian without breaking longitudes", () => {
    const ring = circlePolygon(0, 179.9, 100).geometry.coordinates[0]!;
    expect(ring.every(([lon]) => lon! >= -180 && lon! <= 180)).toBe(true);
    expect(ring[0]).toEqual(ring[ring.length - 1]);
  });

  it("zooms out for bigger uncertainty", () => {
    expect(zoomForRadius(0.05, 40)).toBeGreaterThan(zoomForRadius(50, 40));
    expect(zoomForRadius(5000, 40)).toBeLessThan(2.5);
  });
});
