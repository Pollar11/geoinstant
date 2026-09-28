"use client";

import { AlertTriangle, RotateCcw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Dropzone } from "@/components/Dropzone";
import { FeedbackPanel } from "@/components/FeedbackPanel";
import { LocationMap } from "@/components/LocationMap";
import { PhotoWithRegions } from "@/components/PhotoWithRegions";
import { PipelineTimeline } from "@/components/PipelineTimeline";
import { ResultPanel, ResultSkeleton } from "@/components/ResultPanel";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { sendFeedback } from "@/lib/client";
import { useLocate } from "@/lib/use-locate";

export default function Home() {
  const { phase, stages, result, error, image, elapsed, refining, run, reset } = useLocate();
  const [correcting, setCorrecting] = useState(false);
  const [correction, setCorrection] = useState<[number, number] | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const start = useCallback(
    (f: File) => {
      setCorrecting(false);
      setCorrection(null);
      setNotice(null);
      void run(f);
    },
    [run],
  );

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
            <Button variant="ghost" size="icon" aria-label="Start over" onClick={reset}>
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
            {image && <PhotoWithRegions src={image.previewUrl} regions={result?.regions ?? []} />}
            <Card>
              <CardContent className="pt-4">
                <PipelineTimeline stages={stages} phase={phase} elapsed={elapsed} />
              </CardContent>
            </Card>
          </div>

          <div className="flex min-w-0 flex-col gap-4">
            <Card className="h-[45dvh] min-h-80 overflow-hidden lg:h-[28rem]">
              <LocationMap result={result} correcting={correcting} correction={correction} onCorrect={setCorrection} />
            </Card>

            {error && (
              <Card className="border-danger/40">
                <CardContent className="flex items-center gap-3 pt-4 text-sm">
                  <AlertTriangle className="size-5 text-danger" />
                  <span className="flex-1">{error}</span>
                  <Button size="sm" variant="outline" onClick={reset}>
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
        Estimates come with honest uncertainty: a single photo rarely pins an exact street. Please don&apos;t use GeoInstant to
        locate people without their consent. Place names ©{" "}
        <a className="underline" href="https://www.geonames.org/" target="_blank" rel="noreferrer">
          GeoNames
        </a>{" "}
        (CC-BY 4.0).
      </footer>
    </main>
  );
}
