"use client";

/** Page state machine: idle → preparing → running → done | error. */
import { useCallback, useEffect, useRef, useState } from "react";

import type { LocateResult, Place, StageName, StageStatus } from "./api-types";
import { ApiError, gpsResult, locate, reverse } from "./client";
import { formatCoord } from "./format";
import { prepareImage, PrepareError, type PreparedImage } from "./prepare-image";

function unnamedPlace(latitude: number, longitude: number): Place {
  const label = formatCoord(latitude, longitude, 5);
  return { name: "", admin1: "", country: "", country_code: "", continent: "", display_name: label, distance_km: 0 };
}

export type Phase = "idle" | "preparing" | "running" | "done" | "error";
export type StageState = { status: StageStatus | "pending"; ms: number | null; detail: string };

export const STAGES: { name: StageName; label: string }[] = [
  { name: "upload", label: "Prepare & upload" },
  { name: "metadata", label: "Photo metadata (EXIF/XMP GPS)" },
  { name: "decode", label: "Decode image" },
  { name: "embedding", label: "Scene embedding" },
  { name: "retrieval", label: "Geocells + similar photos" },
  { name: "detection", label: "Location cues (trees, signs, poles…)" },
  { name: "ocr", label: "Text on signs" },
  { name: "fusion", label: "Evidence fusion" },
  { name: "vlm", label: "Visual reasoning (refines later)" },
];

const initialStages = (): Record<StageName, StageState> =>
  Object.fromEntries(STAGES.map((s) => [s.name, { status: "pending", ms: null, detail: "" }])) as Record<StageName, StageState>;

export function useLocate() {
  const [phase, setPhase] = useState<Phase>("idle");
  const [stages, setStages] = useState(initialStages);
  const [result, setResult] = useState<LocateResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [image, setImage] = useState<PreparedImage | null>(null);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [refining, setRefining] = useState(false);
  const abort = useRef<AbortController | null>(null);

  const mark = useCallback((name: StageName, status: StageState["status"], ms: number | null = null, detail = "") => {
    setStages((s) => ({ ...s, [name]: { status, ms, detail } }));
  }, []);

  // Live elapsed timer while running.
  useEffect(() => {
    if (startedAt == null || (phase !== "running" && phase !== "preparing")) return;
    const id = setInterval(() => setElapsed(performance.now() - startedAt), 50);
    return () => clearInterval(id);
  }, [phase, startedAt]);

  const reset = useCallback(() => {
    abort.current?.abort();
    setImage((prev) => {
      if (prev) URL.revokeObjectURL(prev.previewUrl);
      return null;
    });
    setPhase("idle");
    setStages(initialStages());
    setResult(null);
    setError(null);
    setRefining(false);
    setElapsed(0);
  }, []);

  const run = useCallback(
    async (file: File) => {
      reset();
      const ctrl = new AbortController();
      abort.current = ctrl;
      const t0 = performance.now();
      setStartedAt(t0);
      setPhase("preparing");
      mark("upload", "running");
      try {
        const prepared = await prepareImage(file);
        setImage(prepared);

        if (prepared.kind === "gps") {
          // Instant path: coordinates came from the file header on this device.
          mark("upload", "skipped", null, "not needed");
          mark("metadata", "done", performance.now() - t0, "GPS in photo");
          // Naming the point needs the server; the coordinates themselves don't.
          const place = await reverse(prepared.latitude, prepared.longitude, ctrl.signal).catch(() =>
            unnamedPlace(prepared.latitude, prepared.longitude),
          );
          const ms = performance.now() - t0;
          setResult(gpsResult(prepared.latitude, prepared.longitude, place, prepared.capturedAt, ms));
          for (const s of STAGES) if (s.name !== "upload" && s.name !== "metadata") mark(s.name, "skipped");
          setElapsed(ms);
          setPhase("done");
          return;
        }

        setPhase("running");
        let first = true;
        let answered = false;
        for await (const ev of locate(prepared.blob, prepared.contentType, ctrl.signal)) {
          if (first) {
            mark("upload", "done", performance.now() - t0, `${Math.round(prepared.blob.size / 1024)} KB`);
            first = false;
          }
          switch (ev.type) {
            case "stage":
              mark(ev.stage, ev.status, ev.ms, ev.detail);
              if (ev.stage === "vlm" && ev.status === "running") setRefining(true);
              if (ev.stage === "vlm" && ev.status !== "running") setRefining(false);
              break;
            case "partial":
              setResult(ev.result);
              break;
            case "result":
              mark("fusion", "done", ev.result.timings_ms.fusion ?? null, ev.result.cached ? "cached" : "");
              if (ev.result.cached) {
                // Same photo seen recently: the server answered from its result cache.
                setStages((s) =>
                  Object.fromEntries(
                    Object.entries(s).map(([k, v]) => [k, v.status === "pending" ? { status: "skipped", ms: null, detail: "cached" } : v]),
                  ) as typeof s,
                );
              }
              setResult(ev.result);
              setElapsed(performance.now() - t0);
              setPhase("done");
              answered = true;
              break;
            case "refined":
              setResult(ev.result);
              setRefining(false);
              break;
            case "error":
              throw new ApiError(ev.message, ev.status);
            case "done":
              setRefining(false);
              break;
          }
        }
        if (!answered && !ctrl.signal.aborted) throw new ApiError("The location service stopped before answering. Please try again.", 502);
      } catch (e) {
        if (ctrl.signal.aborted) return;
        setError(e instanceof PrepareError || e instanceof ApiError ? e.message : "Something went wrong. Please try again.");
        setPhase("error");
        setRefining(false);
      }
    },
    [mark, reset],
  );

  useEffect(() => () => abort.current?.abort(), []);

  return { phase, stages, result, error, image, elapsed, refining, run, reset };
}
