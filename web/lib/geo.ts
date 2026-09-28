/** Geometry helpers for the map (no dependencies). */

const R_KM = 6371.0088;
const rad = (d: number) => (d * Math.PI) / 180;
const deg = (r: number) => (r * 180) / Math.PI;

export function haversineKm(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const a =
    Math.sin(rad(lat2 - lat1) / 2) ** 2 + Math.cos(rad(lat1)) * Math.cos(rad(lat2)) * Math.sin(rad(lon2 - lon1) / 2) ** 2;
  return 2 * R_KM * Math.asin(Math.min(1, Math.sqrt(a)));
}

/** Point at `distanceKm` along `bearingDeg` from (lat, lon) on the sphere. */
export function destination(lat: number, lon: number, distanceKm: number, bearingDeg: number): [number, number] {
  const d = distanceKm / R_KM;
  const b = rad(bearingDeg);
  const p1 = rad(lat);
  const p2 = Math.asin(Math.sin(p1) * Math.cos(d) + Math.cos(p1) * Math.sin(d) * Math.cos(b));
  const l2 = rad(lon) + Math.atan2(Math.sin(b) * Math.sin(d) * Math.cos(p1), Math.cos(d) - Math.sin(p1) * Math.sin(p2));
  return [((deg(l2) + 540) % 360) - 180, deg(p2)]; // [lon, lat] for GeoJSON
}

/** A geodesic circle as a GeoJSON polygon ring (lon/lat), for the uncertainty radius. */
export function circlePolygon(lat: number, lon: number, radiusKm: number, steps = 96): GeoJSON.Feature<GeoJSON.Polygon> {
  const r = Math.min(radiusKm, 19_000);
  const ring: [number, number][] = [];
  for (let i = 0; i <= steps; i++) ring.push(destination(lat, lon, r, (i / steps) * 360));
  return { type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [ring] } };
}

/** Web-mercator zoom level at which a circle of `radiusKm` roughly fills a ~400 px viewport. */
export function zoomForRadius(radiusKm: number, latitude: number): number {
  const metersPerPixelAtZ0 = 156543.03392 * Math.cos(rad(latitude));
  const z = Math.log2((metersPerPixelAtZ0 * 400) / Math.max(radiusKm * 1000 * 2.4, 50));
  return Math.max(1, Math.min(17, z));
}
