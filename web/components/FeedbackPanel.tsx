"use client";

import { MapPin } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { LocateResult } from "@/lib/api-types";
import { sendFeedback } from "@/lib/client";
import { formatCoord } from "@/lib/format";
import { blobToBase64, type PreparedImage } from "@/lib/prepare-image";

/** Correction form. Sends embedding-backed feedback; the photo only with consent. */
export function FeedbackPanel({
  result,
  image,
  correction,
  onDone,
}: {
  result: LocateResult;
  image: PreparedImage | null;
  correction: [number, number] | null;
  onDone: (message: string) => void;
}) {
  const [consent, setConsent] = useState(false);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    if (!correction) return;
    setBusy(true);
    setError(null);
    try {
      const imageBase64 = consent && image?.kind === "upload" ? await blobToBase64(image.blob) : null;
      const res = await sendFeedback({
        request_id: result.request_id,
        latitude: correction[0],
        longitude: correction[1],
        was_correct: false,
        comment: comment.trim() || null,
        consent_store_image: consent && imageBase64 != null,
        image_base64: imageBase64,
      });
      onDone(res.accepted ? "Thanks - your correction will be reviewed and used to improve GeoInstant." : "Thanks for the correction.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not send feedback");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <MapPin className="size-4 text-danger" /> Where was it really taken?
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p className="text-muted-foreground">
          {correction ? (
            <>
              Selected <span className="font-mono">{formatCoord(correction[0], correction[1], 5)}</span> - drag the pin to adjust.
            </>
          ) : (
            "Tap the map where the photo was taken."
          )}
        </p>
        <textarea
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          maxLength={1000}
          rows={2}
          placeholder="Optional: what gave it away?"
          className="w-full rounded-md border bg-background p-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
        {image?.kind === "upload" && (
          <label className="flex items-start gap-2">
            <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} className="mt-0.5" />
            <span>
              Also share this photo to improve the model. <span className="text-muted-foreground">Without this, only an anonymous
              image fingerprint and your correction are kept.</span>
            </span>
          </label>
        )}
        {error && <p className="text-danger">{error}</p>}
        <Button onClick={submit} disabled={!correction || busy}>
          {busy ? "Sending…" : "Send correction"}
        </Button>
      </CardContent>
    </Card>
  );
}
