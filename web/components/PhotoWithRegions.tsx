"use client";

import { useState } from "react";

import type { RegionBox } from "@/lib/api-types";
import { cn } from "@/lib/utils";

type Props = {
  src: string;
  regions: RegionBox[];
  skyline?: [number, number][]; // detected ridge (normalised x, y)
  trace?: [number, number][]; // user-traced ridge
  tracing?: boolean;
  onTrace?: (point: [number, number]) => void;
};

/** The photo with evidence boxes, the detected skyline and the user's ridge trace. */
export function PhotoWithRegions({ src, regions, skyline, trace, tracing = false, onTrace }: Props) {
  const [show, setShow] = useState(true);
  const line = (pts: [number, number][]) => pts.map(([x, y]) => `${x},${y}`).join(" ");
  return (
    <figure className="flex justify-center overflow-hidden rounded-lg border bg-muted">
      <div
        className={cn("relative", tracing && "cursor-crosshair")}
        onClick={(e) => {
          if (!tracing || !onTrace) return;
          const r = e.currentTarget.getBoundingClientRect();
          onTrace([(e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height]);
        }}
      >
        {/* eslint-disable-next-line @next/next/no-img-element -- local blob URL, nothing to optimise */}
        <img src={src} alt="Uploaded photo" className="block max-h-[28rem] w-auto max-w-full" draggable={false} />
        {show && (
          <div className="pointer-events-none absolute inset-0">
            {regions.map((r, i) => (
              <div
                key={i}
                className={cn("absolute rounded-sm border-2", r.source === "text" ? "border-amber-400" : r.source === "zoom" ? "border-orange-500" : "border-cyan-400")}
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
            <svg className="absolute inset-0 h-full w-full" viewBox="0 0 1 1" preserveAspectRatio="none">
              {skyline && skyline.length > 1 && !trace?.length && (
                <polyline points={line(skyline)} fill="none" stroke="#22d3ee" strokeWidth={3} vectorEffect="non-scaling-stroke" />
              )}
              {trace && trace.length > 0 && (
                <polyline
                  points={line([...trace].sort((a, b) => a[0] - b[0]))}
                  fill="none"
                  stroke="#f97316"
                  strokeWidth={3}
                  vectorEffect="non-scaling-stroke"
                />
              )}
            </svg>
            {trace?.map(([x, y], i) => (
              <span
                key={i}
                className="absolute size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-orange-500 ring-2 ring-white"
                style={{ left: `${x * 100}%`, top: `${y * 100}%` }}
              />
            ))}
          </div>
        )}
        {tracing && (
          <span className="pointer-events-none absolute top-2 left-2 rounded bg-black/70 px-2 py-1 text-xs text-white">
            Click along the mountain ridge
          </span>
        )}
        {(regions.length > 0 || (skyline?.length ?? 0) > 0) && !tracing ? (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setShow((s) => !s);
            }}
            className="absolute right-2 bottom-2 rounded-full bg-black/60 px-2 py-1 text-xs text-white"
          >
            {show ? "Hide" : "Show"} overlays
          </button>
        ) : null}
      </div>
    </figure>
  );
}
