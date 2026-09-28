import type { Resolution } from "./api-types";

export function formatCoord(lat: number, lon: number, digits = 6): string {
  const ns = lat >= 0 ? "N" : "S";
  const ew = lon >= 0 ? "E" : "W";
  return `${Math.abs(lat).toFixed(digits)}° ${ns}, ${Math.abs(lon).toFixed(digits)}° ${ew}`;
}

/** Decimal places that are honest for a given uncertainty (1e-5 deg ≈ 1.1 m). */
export function digitsForRadius(radiusM: number): number {
  if (radiusM <= 5) return 6;
  if (radiusM <= 50) return 5;
  if (radiusM <= 500) return 4;
  if (radiusM <= 5_000) return 3;
  if (radiusM <= 50_000) return 2;
  return 1;
}

export function formatDistance(meters: number): string {
  if (meters < 1000) return `${Math.round(meters)} m`;
  const km = meters / 1000;
  if (km < 10) return `${km.toFixed(1)} km`;
  return `${Math.round(km).toLocaleString("en-US")} km`;
}

export function formatMs(ms: number | null | undefined): string {
  if (ms == null) return "";
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`;
}

export function formatRatio(x: number): string {
  if (x >= 1000) return "×1000+";
  if (x >= 10) return `×${Math.round(x)}`;
  if (x >= 1) return `×${x.toFixed(1)}`;
  return `÷${(1 / Math.max(x, 1e-3)).toFixed(1)}`;
}

export const RESOLUTION_LABEL: Record<Resolution, string> = {
  exact: "Exact (GPS)",
  street: "Street level",
  city: "City level",
  region: "Region level",
  country: "Country level",
  continent: "Continent level",
  world: "Unknown",
};
