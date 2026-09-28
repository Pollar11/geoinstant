"use client";

import {
  AlertTriangle,
  Camera,
  Globe,
  Image as ImageIcon,
  Images,
  Loader2,
  MapPin,
  MessageSquare,
  Satellite,
  Search,
  Sparkles,
  ZoomIn,
} from "lucide-react";
import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { Investigation, InvestigationReport, InvestigationStep, Nearby } from "@/lib/api-types";
import { nearby } from "@/lib/client";
import { formatCoord } from "@/lib/format";
import { cn } from "@/lib/utils";

const STEP_ICON = {
  note: MessageSquare,
  zoom: ZoomIn,
  search: Search,
  geocode: MapPin,
  reverse: Globe,
  image_search: ImageIcon,
  view: Images,
  error: AlertTriangle,
} as const;
/** Only a verified street/building-level answer counts as a location; anything coarser is a lead. */
export function isPinned(r: InvestigationReport): boolean {
  return (r.precision === "exact" || r.precision === "street") && r.latitude != null && r.longitude != null && r.confidence >= 0.5;
}

const PRECISION: Record<string, string> = {
  exact: "Exact spot",
  street: "Street level",
  neighborhood: "Neighbourhood",
  city: "City",
  region: "Region",
  country: "Country",
  unknown: "Unknown",
};

type Props = {
  steps: InvestigationStep[];
  investigation: Investigation | null;
  running: boolean;
  error: string | null;
  onStart?: (context: string) => void;
  defaultContext?: string;
  street?: "searching" | "found" | "not_found" | null; // the street search that follows up on the lead
};

/** Rainbolt-style investigation: live narration, then "This photo was taken at …" and then & now. */
export function InvestigationPanel({ steps, investigation, running, error, onStart, defaultContext = "", street = null }: Props) {
  const [context, setContext] = useState(defaultContext);
  const report = investigation?.report ?? null;
  const shown = investigation?.steps ?? steps;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Sparkles className="size-4 text-primary" /> Investigation
          {running && <Loader2 className="size-4 animate-spin text-muted-foreground" />}
          {investigation && <span className="text-xs font-normal text-muted-foreground">{investigation.seconds.toFixed(0)} s</span>}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        {report && isPinned(report) && (
          <div className="space-y-2 rounded-lg border border-primary/30 bg-primary/5 p-3">
            <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">📍 Exact location</p>
            <p className="text-xl font-semibold">{report.place_name}</p>
            {report.address && <p className="text-muted-foreground">{report.address}</p>}
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant={report.confidence >= 0.7 ? "success" : report.confidence >= 0.4 ? "warning" : "danger"}>
                {Math.round(report.confidence * 100)}% confident
              </Badge>
              <Badge variant="outline">{PRECISION[report.precision]}</Badge>
              {report.latitude != null && report.longitude != null && (
                <span className="font-mono text-xs">{formatCoord(report.latitude, report.longitude, 5)}</span>
              )}
            </div>
            <p className="leading-relaxed">{report.summary}</p>
            {report.evidence_chain.length > 0 && (
              <ol className="list-decimal space-y-1 pl-5">
                {report.evidence_chain.map((e, i) => (
                  <li key={i}>
                    <span className="font-medium">{e.clue}</span> <span className="text-muted-foreground">→ {e.conclusion}</span>
                  </li>
                ))}
              </ol>
            )}
          </div>
        )}
        {investigation && !running && !(report && isPinned(report)) && street === "found" && report?.place_name && (
          <p>
            <span className="text-muted-foreground">Lead from the clues: </span>
            {report.place_name}
            <span className="text-muted-foreground"> · the street match above pinned the exact spot.</span>
          </p>
        )}
        {investigation && !running && !(report && isPinned(report)) && street !== "found" && (
          <div className="space-y-2 rounded-lg border p-3">
            <p className="font-semibold">
              {street === "searching" ? "Searching street photos for the exact spot…" : "Exact location not found in this photo"}
            </p>
            <p className="text-muted-foreground">
              {street === "searching"
                ? "The clues narrowed it down to the lead below; every street photo around it is being compared with yours."
                : "Nothing visible pins it to a street or building (no readable name, sign, address or known landmark)."}
            </p>
            {report && report.place_name && (
              <p>
                <span className="text-muted-foreground">Lead: </span>
                {report.place_name}
                {report.summary && <span className="text-muted-foreground"> · {report.summary}</span>}
              </p>
            )}
            {street !== "searching" && (
              <p className="text-muted-foreground">
                To pin it: add what you remember below (place, year) and investigate again, or use the mountain skyline match if
                mountains are visible.
              </p>
            )}
          </div>
        )}
        {error && <p className="text-danger">{error}</p>}

        {shown.length > 0 && (
          <details open={running} className="group">
            <summary className="cursor-pointer text-muted-foreground">How it got there ({shown.length} steps)</summary>
            <ol className="mt-2 space-y-1.5">
              {shown.map((s, i) => {
                const Icon = STEP_ICON[s.kind];
                return (
                  <li key={i} className={cn("flex gap-2", s.kind === "error" && "text-danger")}>
                    <Icon className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                    <span className={cn(s.kind === "note" ? "" : "text-muted-foreground")}>{s.text}</span>
                  </li>
                );
              })}
            </ol>
          </details>
        )}

        {report && isPinned(report) && report.latitude != null && report.longitude != null && (
          <ThenAndNow lat={report.latitude} lon={report.longitude} />
        )}

        {onStart && !running && (
          <div className="space-y-2 border-t pt-3">
            <textarea
              value={context}
              onChange={(e) => setContext(e.target.value)}
              rows={2}
              placeholder="Anything the family remembers? (e.g. “Grandpa's trip to Greece, around 1975”)"
              className="w-full rounded-md border bg-background p-2 outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
            <Button size="sm" onClick={() => onStart(context)}>
              <Sparkles /> {investigation ? "Investigate again" : "Investigate"}
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/** Recent street-level photos of the found spot, plus links to Street View and satellite. */
export function ThenAndNow({ lat, lon, heading }: { lat: number; lon: number; heading?: number }) {
  const [data, setData] = useState<Nearby | null>(null);
  useEffect(() => {
    let live = true;
    nearby(lat, lon, heading)
      .then((d) => live && setData(d))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [lat, lon, heading]);
  if (!data) return null;
  return (
    <div className="space-y-2 border-t pt-3">
      <p className="flex items-center gap-2 font-medium">
        <Camera className="size-4" /> The place today
      </p>
      {data.images.length > 0 ? (
        <ul className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {data.images.map((im) => (
            <li key={im.id}>
              <a href={`https://www.mapillary.com/app/?pKey=${im.id}`} target="_blank" rel="noreferrer" className="block">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={im.thumb_url} alt="Recent street photo" className="aspect-[4/3] w-full rounded object-cover" loading="lazy" />
                <span className="text-xs text-muted-foreground">
                  {im.captured_at ?? "date unknown"} · {Math.round(im.distance_m)} m away
                </span>
              </a>
            </li>
          ))}
        </ul>
      ) : (
        data.note && <p className="text-xs text-muted-foreground">{data.note}</p>
      )}
      <div className="flex flex-wrap gap-2">
        <a href={data.street_view_url} target="_blank" rel="noreferrer">
          <Button size="sm" variant="outline">
            <Camera /> Street View here
          </Button>
        </a>
        <a href={data.satellite_url} target="_blank" rel="noreferrer">
          <Button size="sm" variant="outline">
            <Satellite /> Satellite
          </Button>
        </a>
        <a href={data.mapillary_url} target="_blank" rel="noreferrer">
          <Button size="sm" variant="outline">
            <Globe /> Mapillary
          </Button>
        </a>
      </div>
    </div>
  );
}
