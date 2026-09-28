"use client";

import { Building2, Car, Clock, Mountain, Search, Sofa, Sun, Trees, Type, Zap, type LucideIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { Analysis } from "@/lib/api-types";
import { cn } from "@/lib/utils";

const ICON: Record<string, LucideIcon> = {
  text: Type,
  architecture: Building2,
  interior: Sofa,
  infrastructure: Zap,
  vehicles: Car,
  nature: Trees,
  terrain: Mountain,
  light: Sun,
  era: Clock,
};

const SCENE: Record<string, string> = {
  home: "Home",
  bar_restaurant: "Bar / restaurant",
  other_indoor: "Indoors",
  beach_coast: "Beach / coast",
  mountain: "Mountains",
  rural: "Countryside",
  urban: "Town / city",
  other: "Other",
};

const ORDER = { strong: 0, medium: 1, weak: 2 } as const;

/** Rainbolt-style clue board: every clue the reasoning stage found and what it points to. */
export function ClueBoard({ analysis }: { analysis: Analysis }) {
  const clues = [...analysis.clues].sort((a, b) => ORDER[a.strength] - ORDER[b.strength]);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2">
          <Search className="size-4 text-primary" /> Clue board
          <Badge variant="outline">{SCENE[analysis.scene] ?? analysis.scene}</Badge>
          {analysis.era && <Badge variant="outline">{analysis.era}</Badge>}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        {analysis.guesses.length > 0 && (
          <div className="space-y-1.5">
            {analysis.guesses.map((g, i) => (
              <div key={i} className="grid grid-cols-[1fr_3rem] items-center gap-2">
                <div className="relative h-7 overflow-hidden rounded bg-muted">
                  <div className="absolute inset-y-0 left-0 bg-primary/25" style={{ width: `${g.probability * 100}%` }} />
                  <span className="relative block truncate px-2 leading-7">
                    {g.label}
                    {g.latitude != null && <span className="text-muted-foreground"> · ±{Math.round(g.radius_km)} km</span>}
                  </span>
                </div>
                <span className="text-right font-mono text-xs tabular-nums">{Math.round(g.probability * 100)}%</span>
              </div>
            ))}
          </div>
        )}
        {clues.length === 0 ? (
          <p className="text-muted-foreground">No location clues found in this photo.</p>
        ) : (
          <ul className="space-y-2">
            {clues.map((c, i) => {
              const Icon = ICON[c.category] ?? Search;
              return (
                <li key={i} className="flex gap-2">
                  <Icon className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                  <div className="min-w-0 flex-1">
                    <span className="font-medium">{c.clue}</span>
                    <span className="text-muted-foreground"> → {c.implies}</span>
                  </div>
                  <span
                    className={cn(
                      "h-fit rounded px-1.5 py-0.5 text-[10px] font-medium uppercase",
                      c.strength === "strong" && "bg-success/15 text-success",
                      c.strength === "medium" && "bg-warning/20",
                      c.strength === "weak" && "bg-muted text-muted-foreground",
                    )}
                  >
                    {c.strength}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
