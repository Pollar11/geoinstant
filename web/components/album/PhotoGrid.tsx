"use client";

import { Check, Loader2, TriangleAlert } from "lucide-react";

import { imageUrl, type PhotoSummary } from "@/lib/archive";
import { cn } from "@/lib/utils";

const SOURCE_DOT = { you: "bg-green-600", photo: "bg-cyan-600", group: "bg-violet-500" } as const;

export function PhotoGrid({
  photos,
  selected,
  onToggle,
  onOpen,
}: {
  photos: PhotoSummary[];
  selected: Set<string>;
  onToggle: (id: string) => void;
  onOpen: (id: string) => void;
}) {
  if (photos.length === 0) return <p className="py-10 text-center text-sm text-muted-foreground">No photos here yet.</p>;
  return (
    <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-4">
      {photos.map((p) => {
        const busy = p.status === "queued" || p.status === "analyzing";
        const sel = selected.has(p.id);
        return (
          <li key={p.id} className={cn("group relative overflow-hidden rounded-lg border bg-card", sel && "ring-2 ring-primary")}>
            <button type="button" onClick={() => onOpen(p.id)} className="block w-full text-left">
              <div className="relative aspect-[4/3] bg-muted">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={imageUrl(p.id, "thumb")} alt={p.filename} loading="lazy" className="h-full w-full object-cover" />
                {busy && (
                  <span className="absolute inset-0 flex items-center justify-center gap-1 bg-black/40 text-xs text-white">
                    <Loader2 className="size-4 animate-spin" /> {p.status === "queued" ? "Queued" : "Analysing"}
                  </span>
                )}
              </div>
              <div className="space-y-1 p-2 text-xs">
                {p.status === "error" ? (
                  <p className="flex items-center gap-1 text-danger">
                    <TriangleAlert className="size-3" /> Failed
                  </p>
                ) : p.location ? (
                  <p className="flex items-center gap-1.5">
                    <span className={cn("size-2 shrink-0 rounded-full", SOURCE_DOT[p.location.source])} />
                    <span className="truncate font-medium">{p.location.label}</span>
                  </p>
                ) : (
                  !busy && <p className="text-muted-foreground">Not located yet</p>
                )}
                <div className="flex flex-wrap gap-1 text-muted-foreground">
                  {p.era && <span className="rounded bg-muted px-1">{p.era}</span>}
                  {p.group_name && <span className="truncate rounded bg-violet-500/15 px-1 text-violet-700 dark:text-violet-300">{p.group_name}</span>}
                </div>
              </div>
            </button>
            <button
              type="button"
              aria-label={sel ? "Deselect" : "Select"}
              onClick={() => onToggle(p.id)}
              className={cn(
                "absolute top-2 left-2 flex size-6 items-center justify-center rounded-full border-2 border-white bg-black/30 text-white transition-opacity",
                sel ? "bg-primary opacity-100" : "opacity-70 group-hover:opacity-100",
              )}
            >
              {sel && <Check className="size-4" />}
            </button>
          </li>
        );
      })}
    </ul>
  );
}

export function Legend() {
  return (
    <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
      <span className="flex items-center gap-1">
        <span className="size-2 rounded-full bg-cyan-600" /> from the photo
      </span>
      <span className="flex items-center gap-1">
        <span className="size-2 rounded-full bg-violet-500" /> from its group
      </span>
      <span className="flex items-center gap-1">
        <span className="size-2 rounded-full bg-green-600" /> set by you
      </span>
    </div>
  );
}
