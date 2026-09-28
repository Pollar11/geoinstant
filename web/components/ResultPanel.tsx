"use client";

import { Check, Copy, Info, ShieldCheck, Sparkles, ThumbsDown, ThumbsUp } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { EvidenceItem, LocateResult } from "@/lib/api-types";
import { digitsForRadius, formatCoord, formatDistance, formatMs, formatRatio, RESOLUTION_LABEL } from "@/lib/format";
import { cn } from "@/lib/utils";

const SOURCE_LABEL: Record<EvidenceItem["source"], string> = {
  exif: "Metadata",
  classifier: "Scene",
  retrieval: "Similar photos",
  cue: "Visual cue",
  text: "Text",
  vlm: "Reasoning",
};

function confidenceVariant(c: number) {
  if (c >= 75) return "success" as const;
  if (c >= 40) return "warning" as const;
  return "danger" as const;
}

export function ResultPanel({
  result,
  refining,
  onVerdict,
}: {
  result: LocateResult;
  refining: boolean;
  onVerdict: (correct: boolean) => void;
}) {
  const [copied, setCopied] = useState(false);
  const digits = digitsForRadius(result.uncertainty_radius_m);
  const coord = `${result.latitude.toFixed(digits)}, ${result.longitude.toFixed(digits)}`;
  const provisional = result.stage === "partial";

  return (
    <Card className={cn(provisional && "opacity-80")}>
      <CardHeader>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={confidenceVariant(result.confidence)}>{Math.round(result.confidence)}% confident</Badge>
          <Badge variant="outline">{RESOLUTION_LABEL[result.resolution]}</Badge>
          {result.source !== "visual" && <Badge variant="outline">From photo GPS</Badge>}
          {provisional && <Badge variant="outline">Provisional</Badge>}
          {result.stage === "refined" && (
            <Badge>
              <Sparkles className="size-3" /> Refined
            </Badge>
          )}
          {refining && <Badge variant="outline">Refining with visual reasoning…</Badge>}
        </div>
        <h1 className="pt-2 text-2xl font-semibold tracking-tight text-balance">{result.place.display_name}</h1>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
          <span className="font-mono tabular-nums" title={formatCoord(result.latitude, result.longitude)}>
            {coord}
          </span>
          <span>± {formatDistance(result.uncertainty_radius_m)}</span>
          {result.timings_ms.total != null && <span>{formatMs(result.timings_ms.total)} server</span>}
          {result.cached && <span>cached</span>}
          <Button
            size="sm"
            variant="ghost"
            className="h-7 px-2"
            onClick={async () => {
              await navigator.clipboard.writeText(coord);
              setCopied(true);
              setTimeout(() => setCopied(false), 1500);
            }}
          >
            {copied ? <Check /> : <Copy />} {copied ? "Copied" : "Copy"}
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-5">
        <p className="text-sm leading-relaxed">{result.explanation}</p>

        {result.mode === "dev" && result.source === "visual" && (
          <p className="flex gap-2 rounded-md bg-warning/15 p-3 text-xs">
            <Info className="size-4 shrink-0" />
            The service is running without trained models (dev mode), so it can only recognise photos already in its index and
            read text/cues. See the README to install the model artifacts.
          </p>
        )}
        {result.privacy.coarsened && (
          <p className="flex gap-2 rounded-md bg-muted p-3 text-xs">
            <ShieldCheck className="size-4 shrink-0" />
            {result.privacy.reason}
          </p>
        )}

        {result.hierarchy.length > 0 && (
          <section aria-label="Location hierarchy" className="space-y-2">
            <h3 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">How sure, at each level</h3>
            {result.hierarchy.map((h) => (
              <div key={h.level} className="grid grid-cols-[5.5rem_1fr_3rem] items-center gap-2 text-sm">
                <span className="text-muted-foreground capitalize">{h.level}</span>
                <div className="relative h-6 overflow-hidden rounded bg-muted">
                  <div className="absolute inset-y-0 left-0 bg-primary/25" style={{ width: `${h.probability * 100}%` }} />
                  <span className="relative block truncate px-2 leading-6">{h.name || "—"}</span>
                </div>
                <span className="text-right font-mono text-xs tabular-nums">{Math.round(h.probability * 100)}%</span>
              </div>
            ))}
          </section>
        )}

        {result.evidence.length > 0 && (
          <section aria-label="Evidence" className="space-y-2">
            <h3 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">Evidence</h3>
            <ul className="space-y-2">
              {result.evidence.map((e, i) => (
                <li key={i} className="rounded-md border p-2 text-sm">
                  <div className="flex items-center gap-2">
                    <Badge variant="outline">{SOURCE_LABEL[e.source]}</Badge>
                    <span className="flex-1 font-medium">{e.label}</span>
                    <span
                      className={cn(
                        "font-mono text-xs tabular-nums",
                        e.direction === "supports" && "text-success",
                        e.direction === "contradicts" && "text-danger",
                        e.direction === "neutral" && "text-muted-foreground",
                      )}
                      title="How much more likely this evidence makes the answer, compared with the rest of the world"
                    >
                      {e.source === "exif" ? "GPS" : formatRatio(e.likelihood_ratio)}
                    </span>
                  </div>
                  {e.detail && <p className="mt-1 text-xs text-muted-foreground">{e.detail}</p>}
                </li>
              ))}
            </ul>
          </section>
        )}

        {result.candidates.length > 1 && (
          <details className="text-sm">
            <summary className="cursor-pointer text-muted-foreground">Other candidates</summary>
            <ul className="mt-2 space-y-1">
              {result.candidates.map((c, i) => (
                <li key={i} className="flex justify-between gap-2">
                  <span className="truncate">{c.name}</span>
                  <span className="font-mono text-xs tabular-nums">{(c.probability * 100).toFixed(1)}%</span>
                </li>
              ))}
            </ul>
          </details>
        )}

        {!provisional && (
          <div className="flex flex-wrap items-center gap-2 border-t pt-4">
            <span className="text-sm text-muted-foreground">Is this right?</span>
            <Button size="sm" variant="outline" onClick={() => onVerdict(true)}>
              <ThumbsUp /> Yes
            </Button>
            <Button size="sm" variant="outline" onClick={() => onVerdict(false)}>
              <ThumbsDown /> No, correct it
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export function ResultSkeleton() {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-muted-foreground">Waiting for the first estimate…</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="h-7 w-2/3 animate-pulse rounded bg-muted" />
        <div className="h-4 w-1/2 animate-pulse rounded bg-muted" />
        <div className="h-16 animate-pulse rounded bg-muted" />
      </CardContent>
    </Card>
  );
}
