"use client";

import { AlertTriangle, RotateCcw } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { Dropzone } from "@/components/Dropzone";
import { FeedbackPanel } from "@/components/FeedbackPanel";
import { LocationMap } from "@/components/LocationMap";
import { PhotoWithRegions } from "@/components/PhotoWithRegions";
import { PipelineTimeline } from "@/components/PipelineTimeline";
import { ResultPanel, ResultSkeleton } from "@/components/ResultPanel";
import { SkylinePanel } from "@/components/SkylinePanel";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import type { BBox, SkylineResult } from "@/lib/api-types";
import { sendFeedback, skylineSearch } from "@/lib/client";
import { useLocate } from "@/lib/use-locate";

export default function Home() {
  const { phase, stages, result, error, image, elapsed, refining, run, reset } = useLocate();
  const [correcting, setCorrecting] = useState(false);
  const [correction, setCorrection] = useState<[number, number] | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Skyline (mountain) matching
  const [bounds, setBounds] = useState<BBox | null>(null);
  const [tracing, setTracing] = useState(false);
  const [trace, setTrace] = useState<[number, number][]>([]);
  const [sky, setSky] = useState<SkylineResult | null>(null);
  const [skyBusy, setSkyBusy] = useState(false);
  const [skyError, setSkyError] = useState<string | null>(null);
  const [selected, setSelected] = useState(0);

  const start = useCallback(
    (f: File) => {
      setCorrecting(false);
      setCorrection(null);
      setNotice(null);
      setTracing(false);
      setTrace([]);
      setSky(null);
      setSkyError(null);
      void run(f);
    },
    [run],
  );

  const views = useMemo(
    () => (sky?.candidates ?? []).map((c) => ({ latitude: c.latitude, longitude: c.longitude, azimuth: c.azimuth_deg, fov: c.fov_deg })),
    [sky],
  );
  const skylineLine = useMemo(() => sky?.profile.filter((p) => p[2] > 0).map((p) => [p[0], p[1]] as [number, number]), [sky]);
  const startOver = useCallback(() => {
    setTracing(false);
    setTrace([]);
    setSky(null);
    setSkyError(null);
    reset();
  }, [reset]);

  const searchSkyline = useCallback(async () => {
    if (!image || !bounds) return;
    setSkyBusy(true);
    setSkyError(null);
    setTracing(false);
    try {
      setSky(await skylineSearch(image.blob, bounds, trace.length >= 3 ? trace : null));
      setSelected(0);
    } catch (e) {
      setSkyError(e instanceof Error ? e.message : "Skyline search failed");
    } finally {
      setSkyBusy(false);
    }
  }, [image, bounds, trace]);

  // Android share sheet → service worker stashed the photo (see public/sw.js).
  useEffect(() => {
    if (!new URLSearchParams(window.location.search).has("shared") || !("caches" in window)) return;
    void (async () => {
      const res = await (await caches.open("geoinstant-share")).match("/shared-image");
      await caches.delete("geoinstant-share");
      window.history.replaceState(null, "", "/");
      if (res) {
        const blob = await res.blob();
        start(new File([blob], "shared-photo", { type: blob.type }));
      }
    })();
  }, [start]);

  const verdict = useCallback(
    (correct: boolean) => {
      if (!result) return;
      if (correct) {
        setCorrecting(false);
        setNotice("Thanks for confirming!");
        if (!result.request_id.startsWith("local-")) {
          void sendFeedback({
            request_id: result.request_id,
            latitude: result.latitude,
            longitude: result.longitude,
            was_correct: true,
            consent_store_image: false,
          }).catch(() => undefined);
        }
      } else {
        setNotice(null);
        setCorrecting(true);
      }
    },
    [result],
  );

  const idle = phase === "idle";

  return (
    <main className="mx-auto flex min-h-dvh max-w-7xl flex-col gap-4 p-3 sm:p-6">
      <header className="flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/icon.svg" alt="" className="size-8" />
          <div>
            <p className="text-lg leading-tight font-semibold">GeoInstant</p>
            <p className="hidden text-xs text-muted-foreground sm:block">Where was this photo taken?</p>
          </div>
        </div>
        {!idle && (
          <div className="flex shrink-0 gap-1 sm:gap-2">
            <Dropzone onFile={start} compact />
            <Button variant="ghost" size="icon" aria-label="Start over" onClick={startOver}>
              <RotateCcw />
            </Button>
          </div>
        )}
      </header>

      {idle ? (
        <Dropzone onFile={start} />
      ) : (
        <div className="grid flex-1 gap-4 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
          <div className="flex min-w-0 flex-col gap-4">
            {image && (
              <PhotoWithRegions
                src={image.previewUrl}
                regions={result?.regions ?? []}
                skyline={skylineLine}
                trace={trace}
                tracing={tracing}
                onTrace={(pt) => setTrace((t) => [...t, pt])}
              />
            )}
            {image && phase !== "preparing" && (
              <SkylinePanel
                bounds={bounds}
                maxAreaKm2={5000}
                tracing={tracing}
                tracePoints={trace.length}
                busy={skyBusy}
                result={sky}
                error={skyError}
                selected={selected}
                onTraceToggle={() => setTracing((t) => !t)}
                onTraceClear={() => setTrace([])}
                onSearch={() => void searchSkyline()}
                onSelect={setSelected}
              />
            )}
            <Card>
              <CardContent className="pt-4">
                <PipelineTimeline stages={stages} phase={phase} elapsed={elapsed} />
              </CardContent>
            </Card>
          </div>

          <div className="flex min-w-0 flex-col gap-4">
            <Card className="h-[45dvh] min-h-80 overflow-hidden lg:h-[28rem]">
              <LocationMap
                result={result}
                correcting={correcting}
                correction={correction}
                onCorrect={setCorrection}
                onBounds={setBounds}
                views={views}
                selectedView={selected}
                heat={sky?.heat}
              />
            </Card>

            {error && (
              <Card className="border-danger/40">
                <CardContent className="flex items-center gap-3 pt-4 text-sm">
                  <AlertTriangle className="size-5 text-danger" />
                  <span className="flex-1">{error}</span>
                  <Button size="sm" variant="outline" onClick={startOver}>
                    Try another photo
                  </Button>
                </CardContent>
              </Card>
            )}
            {notice && <p className="rounded-md bg-success/15 p-3 text-sm">{notice}</p>}
            {correcting && result && (
              <FeedbackPanel
                result={result}
                image={image}
                correction={correction}
                onDone={(m) => {
                  setCorrecting(false);
                  setNotice(m);
                }}
              />
            )}
            {result ? <ResultPanel result={result} refining={refining} onVerdict={verdict} /> : !error && <ResultSkeleton />}
          </div>
        </div>
      )}

      <footer className="pt-2 text-center text-xs text-muted-foreground">
        Estimates include uncertainty. Place names ©{" "}
        <a className="underline" href="https://www.geonames.org/" target="_blank" rel="noreferrer">
          GeoNames
        </a>{" "}
        (CC-BY 4.0).
      </footer>
    </main>
  );
}
