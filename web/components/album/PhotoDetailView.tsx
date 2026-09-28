"use client";

import { ArrowLeft, Check, MapPin, RefreshCw, Trash2, X } from "lucide-react";
import { useEffect, useState } from "react";

import { ClueBoard } from "@/components/ClueBoard";
import { InvestigationPanel, ThenAndNow } from "@/components/InvestigationPanel";
import { PhotoWithRegions } from "@/components/PhotoWithRegions";
import { ResultPanel } from "@/components/ResultPanel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { imageUrl, type Group, type PhotoDetail } from "@/lib/archive";
import { formatCoord } from "@/lib/format";

const SOURCE_LABEL = { you: "Set by you", photo: "From this photo", group: "From its group" } as const;

type Props = {
  photo: PhotoDetail;
  groups: Group[];
  picking: boolean;
  skyline?: [number, number][];
  trace: [number, number][];
  tracing: boolean;
  onTrace: (pt: [number, number]) => void;
  onBack: () => void;
  onPickToggle: () => void;
  onPatch: (body: Record<string, unknown>) => Promise<void>;
  onDelete: () => void;
  onReanalyze: () => void;
  skylinePanel: React.ReactNode;
};

export function PhotoDetailView(p: Props) {
  const { photo } = p;
  const [note, setNote] = useState(photo.note ?? "");
  useEffect(() => setNote(photo.note ?? ""), [photo.id, photo.note]);
  const loc = photo.location;
  const r = photo.result;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-2">
        <Button variant="ghost" size="sm" onClick={p.onBack}>
          <ArrowLeft /> All photos
        </Button>
        <span className="truncate text-sm text-muted-foreground">{photo.filename}</span>
      </div>

      <PhotoWithRegions
        src={imageUrl(photo.id)}
        regions={r?.regions ?? []}
        skyline={p.skyline}
        trace={p.trace}
        tracing={p.tracing}
        onTrace={p.onTrace}
      />

      <Card>
        <CardContent className="space-y-3 pt-4 text-sm">
          {photo.status !== "done" && photo.status !== "error" && <p className="text-muted-foreground">Analysing this photo…</p>}
          {photo.status === "error" && <p className="text-danger">Analysis failed: {photo.error}</p>}
          {loc ? (
            <div className="space-y-1">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant={loc.source === "you" ? "success" : "default"}>{SOURCE_LABEL[loc.source]}</Badge>
                {loc.source !== "you" && <Badge variant="outline">{Math.round(loc.confidence)}% · {loc.resolution}</Badge>}
              </div>
              <p className="text-lg font-semibold">{loc.label}</p>
              <p className="font-mono text-xs text-muted-foreground">{formatCoord(loc.latitude, loc.longitude, 4)}</p>
            </div>
          ) : (
            photo.status === "done" && (
              <div className="space-y-1">
                <p className="font-semibold">Exact location not found yet</p>
                {photo.lead && <p className="text-muted-foreground">Lead: {photo.lead}</p>}
                <p className="text-muted-foreground">Link it to a photo from the same day, add what you remember in the notes, or set it yourself.</p>
              </div>
            )
          )}
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant={p.picking ? "default" : "outline"} onClick={p.onPickToggle}>
              <MapPin /> {p.picking ? "Click the map…" : "Set location on map"}
            </Button>
            {loc && loc.source !== "you" && (
              <Button size="sm" variant="outline" onClick={() => void p.onPatch({ user_lat: loc.latitude, user_lon: loc.longitude, user_label: loc.label })}>
                <Check /> Confirm
              </Button>
            )}
            {loc?.source === "you" && (
              <Button size="sm" variant="ghost" onClick={() => void p.onPatch({ clear_location: true })}>
                <X /> Remove my pin
              </Button>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <label className="text-muted-foreground" htmlFor="group">
              Same place/event as:
            </label>
            <select
              id="group"
              value={photo.group_id ?? ""}
              onChange={(e) => void p.onPatch({ group_id: e.target.value || null })}
              className="rounded-md border bg-background px-2 py-1"
            >
              <option value="">— no group —</option>
              {p.groups.map((g) => (
                <option key={g.id} value={g.id}>
                  {g.name} ({g.photo_ids.length})
                </option>
              ))}
            </select>
          </div>
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            onBlur={() => note !== (photo.note ?? "") && void p.onPatch({ note })}
            rows={2}
            placeholder="Notes: who, when, what you remember…"
            className="w-full rounded-md border bg-background p-2 outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
          <div className="flex gap-2">
            <Button size="sm" variant="ghost" onClick={p.onReanalyze}>
              <RefreshCw /> Analyse again
            </Button>
            <Button size="sm" variant="ghost" className="text-danger" onClick={p.onDelete}>
              <Trash2 /> Delete
            </Button>
          </div>
        </CardContent>
      </Card>

      {(photo.investigation || photo.status === "done") && (
        <InvestigationPanel
          steps={[]}
          investigation={photo.investigation ?? null}
          running={photo.status === "queued" || photo.status === "analyzing"}
          error={null}
          defaultContext={photo.note ?? ""}
          onStart={async (context) => {
            await p.onPatch({ note: context });
            p.onReanalyze();
          }}
        />
      )}
      {loc?.source === "you" && ["exact", "street"].includes(loc.resolution) && (
        <Card>
          <CardContent className="pt-4 text-sm">
            <ThenAndNow lat={loc.latitude} lon={loc.longitude} />
          </CardContent>
        </Card>
      )}
      {r?.analysis && <ClueBoard analysis={r.analysis} />}
      {p.skylinePanel}
      {r && r.source === "visual" && (
        <ResultPanel
          result={r}
          refining={false}
          onVerdict={(ok) => (ok ? void p.onPatch({ user_lat: r.latitude, user_lon: r.longitude, user_label: r.place.display_name }) : p.onPickToggle())}
        />
      )}
    </div>
  );
}
