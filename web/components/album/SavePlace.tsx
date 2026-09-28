"use client";

import { Camera, Loader2, MapPinned } from "lucide-react";
import { useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { archive } from "@/lib/archive";

type Fix = { latitude: number; longitude: number; accuracy: number };

function locate(): Promise<Fix> {
  return new Promise((resolve, reject) => {
    if (!navigator.geolocation) return reject(new Error("This browser can't share its location."));
    navigator.geolocation.getCurrentPosition(
      (p) => resolve({ latitude: p.coords.latitude, longitude: p.coords.longitude, accuracy: p.coords.accuracy }),
      (e) =>
        reject(
          new Error(
            e.code === e.PERMISSION_DENIED
              ? "Location is blocked. Allow location for this site in your browser settings, then try again."
              : "Couldn't get your location. Step outside or turn on GPS, then try again.",
          ),
        ),
      { enableHighAccuracy: true, timeout: 30_000, maximumAge: 0 },
    );
  });
}

/** While you're there: take a photo and store it with the phone's exact position, so no one has to search later. */
export function SavePlace({ onSaved }: { onSaved: (id: string) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const fix = useRef<Promise<Fix> | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [where, setWhere] = useState<Fix | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function start() {
    setError(null);
    fix.current = locate(); // ask for the position right away, while the camera opens
    fix.current.then(setWhere).catch((e: Error) => setError(e.message));
    input.current?.click();
  }

  async function save() {
    if (!file || !fix.current) return;
    setBusy(true);
    try {
      const here = await fix.current;
      const r = await archive.upload(file, here);
      const id = r.added[0];
      if (!id) throw new Error(r.skipped[0] ?? "Upload failed");
      if (note.trim()) await archive.patch(id, { note: note.trim() });
      setFile(null);
      setNote("");
      onSaved(id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="w-full space-y-2 rounded-lg border bg-card p-3 text-sm">
      <input
        ref={input}
        type="file"
        accept="image/*"
        capture="environment"
        className="sr-only"
        onChange={(e) => setFile(e.target.files?.[0] ?? null)}
      />
      {!file ? (
        <div className="flex flex-wrap items-center gap-3">
          <Button onClick={start}>
            <MapPinned /> Save this place
          </Button>
          <span className="text-muted-foreground">{"At a place you'll want to remember? Take a photo; it's saved with your exact location."}</span>
        </div>
      ) : (
        <div className="space-y-2">
          <p className="flex items-center gap-2 font-medium">
            <Camera className="size-4" /> {file.name || "New photo"}
          </p>
          <p className="text-muted-foreground">
            {where
              ? `Location captured (±${Math.round(where.accuracy)} m)${where.accuracy > 100 ? " - not very precise; wait a moment outdoors for a better fix" : ""}`
              : !error && "Getting your location…"}
          </p>
          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="What is this? e.g. “Aunt Mary's wedding, Lake Como”"
            className="w-full rounded-md border bg-background p-2 outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
          <div className="flex gap-2">
            <Button onClick={() => void save()} disabled={busy || !where}>
              {busy ? <Loader2 className="animate-spin" /> : <MapPinned />} Save
            </Button>
            <Button variant="ghost" onClick={() => setFile(null)} disabled={busy}>
              Cancel
            </Button>
          </div>
        </div>
      )}
      {error && <p className="text-danger">{error}</p>}
    </div>
  );
}
