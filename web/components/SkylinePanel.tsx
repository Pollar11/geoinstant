"use client";

import { Loader2, Mountain, Pencil, RotateCcw, Search } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { BBox, SkylineResult } from "@/lib/api-types";
import { formatCoord } from "@/lib/format";
import { cn } from "@/lib/utils";

const DIRS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
const compass = (deg: number) => DIRS[Math.round(deg / 45) % 8];

export function areaKm2([s, w, n, e]: BBox): number {
  return (n - s) * 111.32 * (e - w) * 111.32 * Math.cos(((s + n) / 2) * (Math.PI / 180));
}

type Props = {
  bounds: BBox | null;
  maxAreaKm2: number;
  tracing: boolean;
  tracePoints: number;
  busy: boolean;
  result: SkylineResult | null;
  error: string | null;
  selected: number;
  onTraceToggle: () => void;
  onTraceClear: () => void;
  onSearch: () => void;
  onSelect: (i: number) => void;
};

/** Mountain skyline matching: pick an area on the map, optionally trace the ridge, search. */
export function SkylinePanel(p: Props) {
  const area = p.bounds ? areaKm2(p.bounds) : 0;
  const tooBig = area > p.maxAreaKm2;
  const r = p.result;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Mountain className="size-4 text-primary" /> Mountain skyline match
        </CardTitle>
        <p className="text-xs text-muted-foreground">
          Compares the ridge line in your photo with 3D terrain to find where it was taken and which way the camera faced.
        </p>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <ol className="list-decimal space-y-1 pl-5 text-muted-foreground">
          <li>Zoom the map to the area you suspect (e.g. a valley or mountain range).</li>
          <li>Optional: trace the ridge on the photo if people or trees cover it.</li>
          <li>Search.</li>
        </ol>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant={p.tracing ? "default" : "outline"} onClick={p.onTraceToggle}>
            <Pencil /> {p.tracing ? "Done tracing" : "Trace ridge"}
          </Button>
          {p.tracePoints > 0 && (
            <Button size="sm" variant="ghost" onClick={p.onTraceClear}>
              <RotateCcw /> Clear trace ({p.tracePoints})
            </Button>
          )}
          <Button size="sm" onClick={p.onSearch} disabled={p.busy || !p.bounds || tooBig}>
            {p.busy ? <Loader2 className="animate-spin" /> : <Search />} Search map area
          </Button>
        </div>
        <p className={cn("text-xs", tooBig ? "text-danger" : "text-muted-foreground")}>
          Map area: {Math.round(area).toLocaleString("en-US")} km²
          {tooBig && ` - zoom in (max ${p.maxAreaKm2.toLocaleString("en-US")} km²)`}
          {p.busy && " · first search in a new area prepares terrain, up to a minute"}
        </p>
        {p.error && <p className="text-danger">{p.error}</p>}
        {r && r.status !== "ok" && <p className="text-danger">{r.message}</p>}
        {r && r.status === "ok" && (
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant={r.confidence >= 50 ? "success" : r.confidence >= 20 ? "warning" : "danger"}>
                {Math.round(r.confidence)}% distinct match
              </Badge>
              <span className="text-xs text-muted-foreground">
                {r.viewpoints.toLocaleString("en-US")} viewpoints searched · {((r.timings_ms.total ?? 0) / 1000).toFixed(1)} s
              </span>
            </div>
            {r.confidence < 50 && (
              <p className="text-xs text-muted-foreground">
                Several places fit similarly. Trace a longer stretch of ridge or narrow the area, then search again.
              </p>
            )}
            <ul className="space-y-1.5">
              {r.candidates.map((c, i) => (
                <li key={i}>
                  <button
                    type="button"
                    onClick={() => p.onSelect(i)}
                    className={cn("w-full rounded-md border p-2 text-left hover:bg-muted", p.selected === i && "border-primary bg-primary/5")}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium">
                        {i + 1}. {c.place.display_name}
                      </span>
                      <span className="font-mono text-xs tabular-nums">{Math.round(c.match * 100)}%</span>
                    </div>
                    <div className="text-xs text-muted-foreground">
                      {formatCoord(c.latitude, c.longitude, 4)} · {Math.round(c.elevation_m)} m · facing {compass(c.azimuth_deg)} (
                      {Math.round(c.azimuth_deg)}°) · lens ≈{Math.round(c.fov_deg)}°
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
