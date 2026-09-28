"use client";

import { useState } from "react";

import type { RegionBox } from "@/lib/api-types";
import { cn } from "@/lib/utils";

/** The photo with the image regions that drove the prediction drawn on top. */
export function PhotoWithRegions({ src, regions }: { src: string; regions: RegionBox[] }) {
  const [show, setShow] = useState(true);
  return (
    <figure className="relative overflow-hidden rounded-lg border bg-muted">
      {/* eslint-disable-next-line @next/next/no-img-element -- local blob URL, nothing to optimise */}
      <img src={src} alt="Uploaded photo" className="block max-h-[28rem] w-full object-contain" />
      {show && (
        <div className="pointer-events-none absolute inset-0">
          {regions.map((r, i) => (
            <div
              key={i}
              className={cn(
                "absolute rounded-sm border-2",
                r.source === "text" ? "border-amber-400" : "border-cyan-400",
              )}
              style={{
                left: `${r.box[0] * 100}%`,
                top: `${r.box[1] * 100}%`,
                width: `${(r.box[2] - r.box[0]) * 100}%`,
                height: `${(r.box[3] - r.box[1]) * 100}%`,
              }}
            >
              <span className="absolute -top-5 left-0 max-w-48 truncate rounded bg-black/70 px-1 text-[10px] text-white">
                {r.label} · {Math.round(r.score * 100)}%
              </span>
            </div>
          ))}
        </div>
      )}
      {regions.length > 0 && (
        <button
          type="button"
          onClick={() => setShow((s) => !s)}
          className="absolute right-2 bottom-2 rounded-full bg-black/60 px-2 py-1 text-xs text-white"
        >
          {show ? "Hide" : "Show"} evidence regions
        </button>
      )}
    </figure>
  );
}
