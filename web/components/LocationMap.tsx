"use client";

/** MapLibre map: prediction, uncertainty circle, candidates, click/drag to correct. */
import type { GeoJSONSource, Map as MlMap, MapMouseEvent, Marker } from "maplibre-gl";
import { useEffect, useRef } from "react";

import type { BBox, LocateResult } from "@/lib/api-types";
import { circlePolygon, destination, zoomForRadius } from "@/lib/geo";

const STYLE_URL = process.env.NEXT_PUBLIC_MAP_STYLE_URL || "https://tiles.openfreemap.org/styles/liberty";
const EMPTY: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };
const NO_VIEWS: View[] = [];
const NO_HEAT: [number, number, number][] = [];

export type View = { latitude: number; longitude: number; azimuth: number; fov: number };

type Props = {
  result: LocateResult | null;
  correcting: boolean;
  correction: [number, number] | null; // [lat, lon]
  onCorrect: (latLon: [number, number]) => void;
  onBounds?: (bbox: BBox) => void;
  views?: View[]; // skyline candidates: camera position + viewing cone
  selectedView?: number;
  heat?: [number, number, number][]; // (lat, lon, score)
  found?: { latitude: number; longitude: number } | null; // investigator's answer
};

/** Camera viewing cone as a polygon (8 km long). */
export function cone(v: View, km = 8): GeoJSON.Feature<GeoJSON.Polygon> {
  const ring: [number, number][] = [[v.longitude, v.latitude]];
  for (let i = 0; i <= 12; i++) ring.push(destination(v.latitude, v.longitude, km, v.azimuth - v.fov / 2 + (v.fov * i) / 12));
  ring.push([v.longitude, v.latitude]);
  return { type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [ring] } };
}

export function LocationMap({
  result,
  correcting,
  correction,
  onCorrect,
  onBounds,
  views = NO_VIEWS,
  selectedView = 0,
  heat = NO_HEAT,
  found = null,
}: Props) {
  const foundPin = useRef<Marker | null>(null);
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<MlMap | null>(null);
  const pin = useRef<Marker | null>(null);
  const fix = useRef<Marker | null>(null);
  const ready = useRef<Promise<void> | null>(null);
  const correctingRef = useRef(correcting);
  const onCorrectRef = useRef(onCorrect);
  const onBoundsRef = useRef(onBounds);
  correctingRef.current = correcting;
  onCorrectRef.current = onCorrect;
  onBoundsRef.current = onBounds;

  // Create the map once (maplibre-gl is ~200 KB, so it is loaded only on the client, on demand).
  useEffect(() => {
    let cancelled = false;
    ready.current = (async () => {
      const ml = await import("maplibre-gl");
      if (cancelled || !el.current) return;
      ml.setWorkerUrl("/maplibre/maplibre-gl-worker.mjs"); // copied by scripts/copy-maplibre-worker.mjs
      const m = new ml.Map({ container: el.current, style: STYLE_URL, center: [10, 25], zoom: 1.2, attributionControl: { compact: true } });
      m.addControl(new ml.NavigationControl({ showCompass: false }), "top-right");
      map.current = m;
      pin.current = new ml.Marker({ color: "#0e9fb5" });
      foundPin.current = new ml.Marker({ color: "#f97316", scale: 1.2 });
      const fixMarker = new ml.Marker({ color: "#e0582f", draggable: true });
      fixMarker.on("dragend", () => {
        const p = fixMarker.getLngLat();
        onCorrectRef.current([p.lat, p.lng]);
      });
      fix.current = fixMarker;
      m.on("click", (e: MapMouseEvent) => {
        if (correctingRef.current) onCorrectRef.current([e.lngLat.lat, e.lngLat.lng]);
      });
      const emitBounds = () => {
        const b = m.getBounds();
        onBoundsRef.current?.([b.getSouth(), b.getWest(), b.getNorth(), b.getEast()]);
      };
      m.on("moveend", emitBounds);
      emitBounds();
      await new Promise<void>((resolve) => (m.loaded() ? resolve() : m.once("load", () => resolve())));
      m.addSource("uncertainty", { type: "geojson", data: EMPTY });
      m.addLayer({ id: "uncertainty-fill", type: "fill", source: "uncertainty", paint: { "fill-color": "#0e9fb5", "fill-opacity": 0.12 } });
      m.addLayer({ id: "uncertainty-line", type: "line", source: "uncertainty", paint: { "line-color": "#0e9fb5", "line-width": 1.5 } });
      m.addSource("candidates", { type: "geojson", data: EMPTY });
      m.addLayer({
        id: "candidates",
        type: "circle",
        source: "candidates",
        paint: { "circle-radius": 4, "circle-color": "#6b7280", "circle-stroke-color": "#fff", "circle-stroke-width": 1 },
      });
      m.addSource("heat", { type: "geojson", data: EMPTY });
      m.addLayer({
        id: "heat",
        type: "circle",
        source: "heat",
        paint: {
          "circle-radius": 3,
          "circle-color": ["interpolate", ["linear"], ["get", "s"], 0, "#fde68a", 1, "#dc2626"],
          "circle-opacity": ["interpolate", ["linear"], ["get", "s"], 0, 0.15, 1, 0.9],
        },
      });
      m.addSource("views", { type: "geojson", data: EMPTY });
      m.addLayer({
        id: "views",
        type: "fill",
        source: "views",
        paint: { "fill-color": ["case", ["get", "sel"], "#f97316", "#64748b"], "fill-opacity": ["case", ["get", "sel"], 0.35, 0.15] },
      });
    })();
    return () => {
      cancelled = true;
      map.current?.remove();
      map.current = null;
    };
  }, []);

  // Draw the current result.
  useEffect(() => {
    let stale = false;
    void ready.current?.then(() => {
      const m = map.current;
      if (stale || !m) return;
      const circle = m.getSource("uncertainty") as GeoJSONSource | undefined;
      const cands = m.getSource("candidates") as GeoJSONSource | undefined;
      if (!result) {
        pin.current?.remove();
        circle?.setData(EMPTY);
        cands?.setData(EMPTY);
        return;
      }
      const radiusKm = result.uncertainty_radius_m / 1000;
      pin.current?.setLngLat([result.longitude, result.latitude]).addTo(m);
      circle?.setData(circlePolygon(result.latitude, result.longitude, radiusKm));
      cands?.setData({
        type: "FeatureCollection",
        features: result.candidates.slice(1).map((c) => ({
          type: "Feature",
          properties: { name: c.name },
          geometry: { type: "Point", coordinates: [c.longitude, c.latitude] },
        })),
      });
      m.flyTo({ center: [result.longitude, result.latitude], zoom: zoomForRadius(radiusKm, result.latitude), speed: 1.6 });
    });
    return () => {
      stale = true;
    };
  }, [result]);

  // Skyline candidates: heat of all viewpoints, cones for the top matches.
  useEffect(() => {
    void ready.current?.then(() => {
      const m = map.current;
      if (!m) return;
      (m.getSource("heat") as GeoJSONSource | undefined)?.setData({
        type: "FeatureCollection",
        features: heat.map(([lat, lon, s]) => ({ type: "Feature", properties: { s }, geometry: { type: "Point", coordinates: [lon, lat] } })),
      });
      (m.getSource("views") as GeoJSONSource | undefined)?.setData({
        type: "FeatureCollection",
        features: views.map((v, i) => ({ ...cone(v), properties: { sel: i === selectedView } })),
      });
      const v = views[selectedView];
      if (v) m.flyTo({ center: [v.longitude, v.latitude], zoom: 11, speed: 1.6 });
    });
  }, [views, selectedView, heat]);

  // Investigator's answer: orange pin, zoom to street level.
  const fLat = found?.latitude;
  const fLon = found?.longitude;
  useEffect(() => {
    void ready.current?.then(() => {
      const m = map.current;
      if (!m || !foundPin.current) return;
      if (fLat == null || fLon == null) {
        foundPin.current.remove();
        return;
      }
      foundPin.current.setLngLat([fLon, fLat]).addTo(m);
      m.flyTo({ center: [fLon, fLat], zoom: 16, speed: 1.4 });
    });
  }, [fLat, fLon]);

  // Correction pin.
  useEffect(() => {
    void ready.current?.then(() => {
      const m = map.current;
      if (!m || !fix.current) return;
      if (correcting && correction) fix.current.setLngLat([correction[1], correction[0]]).addTo(m);
      else fix.current.remove();
      m.getCanvas().style.cursor = correcting ? "crosshair" : "";
    });
  }, [correcting, correction]);

  return <div ref={el} className="h-full min-h-80 w-full" role="region" aria-label="Map of the predicted location" />;
}
