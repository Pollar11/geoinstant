"use client";

/** MapLibre map: prediction, uncertainty circle, candidates, click/drag to correct. */
import type { GeoJSONSource, Map as MlMap, MapMouseEvent, Marker } from "maplibre-gl";
import { useEffect, useRef } from "react";

import type { LocateResult } from "@/lib/api-types";
import { circlePolygon, zoomForRadius } from "@/lib/geo";

const STYLE_URL = process.env.NEXT_PUBLIC_MAP_STYLE_URL || "https://tiles.openfreemap.org/styles/liberty";
const EMPTY: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };

type Props = {
  result: LocateResult | null;
  correcting: boolean;
  correction: [number, number] | null; // [lat, lon]
  onCorrect: (latLon: [number, number]) => void;
};

export function LocationMap({ result, correcting, correction, onCorrect }: Props) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<MlMap | null>(null);
  const pin = useRef<Marker | null>(null);
  const fix = useRef<Marker | null>(null);
  const ready = useRef<Promise<void> | null>(null);
  const correctingRef = useRef(correcting);
  const onCorrectRef = useRef(onCorrect);
  correctingRef.current = correcting;
  onCorrectRef.current = onCorrect;

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
      const fixMarker = new ml.Marker({ color: "#e0582f", draggable: true });
      fixMarker.on("dragend", () => {
        const p = fixMarker.getLngLat();
        onCorrectRef.current([p.lat, p.lng]);
      });
      fix.current = fixMarker;
      m.on("click", (e: MapMouseEvent) => {
        if (correctingRef.current) onCorrectRef.current([e.lngLat.lat, e.lngLat.lng]);
      });
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
