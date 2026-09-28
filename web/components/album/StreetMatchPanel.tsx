"use client";

import { Check, ExternalLink, Loader2, ScanSearch } from "lucide-react";

import { areaKm2 } from "@/components/SkylinePanel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import type { BBox } from "@/lib/api-types";
import type { StreetJob, StreetMatch, StreetResult } from "@/lib/archive";
import { cn } from "@/lib/utils";

export const STREET_MAX_KM2 = 6;

type Props = {
  photoUrl: string;
  bounds: BBox | null;
  job: StreetJob | null;
  result: StreetResult | null;
  error: string | null;
  selected: number;
  onSearch: () => void;
  onSelect: (i: number) => void;
  onUse: (m: StreetMatch) => void;
};

/** GeoSpy-style: compare the photo with every street photo in the map view; a verified match is the exact spot. */
export function StreetMatchPanel(p: Props) {
  const area = p.bounds ? areaKm2(p.bounds) : 0;
  const tooBig = area > STREET_MAX_KM2;
  const running = p.job != null && p.job.status !== "done" && p.job.status !== "error";
  const r = p.result;
  const m = r?.candidates[p.selected] ?? null;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ScanSearch className="size-4 text-primary" /> Street match
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p className="text-muted-foreground">
          Zoom the map to the area you suspect (a few streets or a village, up to {STREET_MAX_KM2} km²). Every street-level
          photo there is compared with yours, window by window.
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" onClick={p.onSearch} disabled={!p.bounds || tooBig || running}>
            {running ? <Loader2 className="animate-spin" /> : <ScanSearch />} Search this map area
          </Button>
          <span className={cn("text-xs", tooBig ? "text-danger" : "text-muted-foreground")}>
            {area < 10 ? area.toFixed(1) : Math.round(area).toLocaleString()} km² {tooBig && "· zoom in"}
          </span>
        </div>

        {running && p.job && (
          <div className="space-y-1">
            <Progress value={p.job.progress * 100} label="Street match progress" />
            <p className="text-xs text-muted-foreground">{p.job.message}</p>
          </div>
        )}
        {p.error && <p className="text-danger">{p.error}</p>}

        {r && (
          <div className="space-y-3 border-t pt-3">
            <div className="flex flex-wrap items-center gap-2">
              {r.verified ? <Badge variant="success">📍 Same place found</Badge> : <Badge variant="outline">Not confirmed</Badge>}
              <span className="text-xs text-muted-foreground">{r.searched.toLocaleString()} street photos compared</span>
            </div>
            <p>{r.message}</p>
            {m && (
              <>
                <div className="grid grid-cols-2 gap-2">
                  <figure>
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={p.photoUrl} alt="Your photo" className="aspect-[4/3] w-full rounded object-cover" />
                    <figcaption className="text-xs text-muted-foreground">Then</figcaption>
                  </figure>
                  <figure>
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={m.image_url} alt="Matching street photo" className="aspect-[4/3] w-full rounded object-cover" />
                    <figcaption className="text-xs text-muted-foreground">
                      Now · {m.captured_at || "date unknown"} · {m.inliers} matching points
                    </figcaption>
                  </figure>
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button size="sm" onClick={() => p.onUse(m)}>
                    <Check /> Use this spot
                  </Button>
                  <a href={`https://www.mapillary.com/app/?pKey=${m.image_id}&focus=photo`} target="_blank" rel="noreferrer">
                    <Button size="sm" variant="outline">
                      <ExternalLink /> Look around here
                    </Button>
                  </a>
                </div>
              </>
            )}
            {r.candidates.length > 1 && (
              <ul className="flex gap-2 overflow-x-auto pb-1">
                {r.candidates.map((c, i) => (
                  <li key={c.image_id} className="shrink-0">
                    <button
                      type="button"
                      onClick={() => p.onSelect(i)}
                      className={cn("block w-24 rounded border-2", i === p.selected ? "border-orange-500" : "border-transparent")}
                    >
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img src={c.image_url} alt={`Candidate ${i + 1}`} className="aspect-[4/3] w-full rounded-sm object-cover" loading="lazy" />
                      <span className="text-[10px] text-muted-foreground">{c.inliers} points</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
        <p className="text-xs text-muted-foreground">
          Needs street photos of the area on Mapillary and a place that still looks similar (same buildings, windows,
          rooflines).
        </p>
      </CardContent>
    </Card>
  );
}
