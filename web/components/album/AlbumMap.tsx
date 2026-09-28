"use client";

/** Album map: every located photo, the selected one, skyline cones, and click-to-set-location. */
import type { GeoJSONSource, Map as MlMap, MapLayerMouseEvent, MapMouseEvent } from "maplibre-gl";
import { useEffect, useRef } from "react";

import { cone, type View } from "@/components/LocationMap";
import type { BBox } from "@/lib/api-types";
import type { PhotoSummary } from "@/lib/archive";
import { circlePolygon, zoomForRadius } from "@/lib/geo";

const STYLE_URL = process.env.NEXT_PUBLIC_MAP_STYLE_URL || "https://tiles.openfreemap.org/styles/liberty";
const EMPTY: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };
const NONE: View[] = [];

type Props = {
  photos: PhotoSummary[];
  selectedId: string | null;
  radiusKm?: number; // uncertainty of the selected photo's own answer
  picking: boolean;
  onPick: (latLon: [number, number]) => void;
  onSelect: (id: string) => void;
  onBounds?: (b: BBox) => void;
  views?: View[];
  selectedView?: number;
};

export function AlbumMap({ photos, selectedId, radiusKm, picking, onPick, onSelect, onBounds, views = NONE, selectedView = 0 }: Props) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<MlMap | null>(null);
  const ready = useRef<Promise<void> | null>(null);
  const cb = useRef({ picking, onPick, onSelect, onBounds });
  cb.current = { picking, onPick, onSelect, onBounds };

  useEffect(() => {
    let cancelled = false;
    ready.current = (async () => {
      const ml = await import("maplibre-gl");
      if (cancelled || !el.current) return;
      ml.setWorkerUrl("/maplibre/maplibre-gl-worker.mjs");
      const m = new ml.Map({ container: el.current, style: STYLE_URL, center: [10, 30], zoom: 1.3, attributionControl: { compact: true } });
      m.addControl(new ml.NavigationControl({ showCompass: false }), "top-right");
      map.current = m;
      const emit = () => {
        const b = m.getBounds();
        cb.current.onBounds?.([b.getSouth(), b.getWest(), b.getNorth(), b.getEast()]);
      };
      m.on("moveend", emit);
      emit();
      m.on("click", (e: MapMouseEvent) => {
        if (cb.current.picking) cb.current.onPick([e.lngLat.lat, e.lngLat.lng]);
      });
      await new Promise<void>((resolve) => (m.loaded() ? resolve() : m.once("load", () => resolve())));
      m.addSource("radius", { type: "geojson", data: EMPTY });
      m.addLayer({ id: "radius", type: "fill", source: "radius", paint: { "fill-color": "#0e9fb5", "fill-opacity": 0.12 } });
      m.addSource("views", { type: "geojson", data: EMPTY });
      m.addLayer({
        id: "views",
        type: "fill",
        source: "views",
        paint: { "fill-color": ["case", ["get", "sel"], "#f97316", "#64748b"], "fill-opacity": ["case", ["get", "sel"], 0.35, 0.15] },
      });
      m.addSource("photos", { type: "geojson", data: EMPTY });
      m.addLayer({
        id: "photos",
        type: "circle",
        source: "photos",
        paint: {
          "circle-radius": ["case", ["get", "sel"], 9, 6],
          "circle-color": ["match", ["get", "src"], "you", "#16a34a", "group", "#8b5cf6", "#0e9fb5"],
          "circle-stroke-color": ["case", ["get", "sel"], "#f97316", "#ffffff"],
          "circle-stroke-width": ["case", ["get", "sel"], 3, 1.5],
        },
      });
      m.on("click", "photos", (e: MapLayerMouseEvent) => {
        const id = e.features?.[0]?.properties?.id;
        if (id && !cb.current.picking) cb.current.onSelect(String(id));
      });
      m.on("mouseenter", "photos", () => (m.getCanvas().style.cursor = "pointer"));
      m.on("mouseleave", "photos", () => (m.getCanvas().style.cursor = cb.current.picking ? "crosshair" : ""));
    })();
    return () => {
      cancelled = true;
      map.current?.remove();
      map.current = null;
    };
  }, []);

  useEffect(() => {
    void ready.current?.then(() => {
      const m = map.current;
      if (!m) return;
      (m.getSource("photos") as GeoJSONSource | undefined)?.setData({
        type: "FeatureCollection",
        features: photos
          .filter((p) => p.location)
          .map((p) => ({
            type: "Feature",
            properties: { id: p.id, src: p.location!.source, sel: p.id === selectedId },
            geometry: { type: "Point", coordinates: [p.location!.longitude, p.location!.latitude] },
          })),
      });
    });
  }, [photos, selectedId]);

  // Fly to the selected photo when the selection changes.
  const sel = photos.find((p) => p.id === selectedId);
  const selLat = sel?.location?.latitude;
  const selLon = sel?.location?.longitude;
  useEffect(() => {
    void ready.current?.then(() => {
      const m = map.current;
      if (!m) return;
      const r = radiusKm ?? 0;
      (m.getSource("radius") as GeoJSONSource | undefined)?.setData(
        selLat != null && selLon != null && r > 0.05 ? circlePolygon(selLat, selLon, r) : EMPTY,
      );
      if (selLat != null && selLon != null) m.flyTo({ center: [selLon, selLat], zoom: Math.min(zoomForRadius(Math.max(r, 2), selLat), 13) });
    });
  }, [selectedId, selLat, selLon, radiusKm]);

  useEffect(() => {
    void ready.current?.then(() => {
      const m = map.current;
      if (!m) return;
      (m.getSource("views") as GeoJSONSource | undefined)?.setData({
        type: "FeatureCollection",
        features: views.map((v, i) => ({ ...cone(v), properties: { sel: i === selectedView } })),
      });
      const v = views[selectedView];
      if (v) m.flyTo({ center: [v.longitude, v.latitude], zoom: v.km && v.km < 1 ? 17 : 11 });
    });
  }, [views, selectedView]);

  useEffect(() => {
    void ready.current?.then(() => {
      if (map.current) map.current.getCanvas().style.cursor = picking ? "crosshair" : "";
    });
  }, [picking]);

  return <div ref={el} className="h-full w-full" role="region" aria-label="Album map" />;
}
