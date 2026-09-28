"use client";

import { Camera, Loader2, MapPinned, Search } from "lucide-react";
import { useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { archive } from "@/lib/archive";

type Fix = { latitude: number; longitude: number; accuracy: number };
type Hit = { name: string; latitude: number; longitude: number };
const ROUGH_M = 300; // network-only positions on phones without GPS are often this coarse

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
  // No GPS (or too rough): type where you are instead.
  const [typing, setTyping] = useState(false);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<Hit[] | null>(null);
  const [chosen, setChosen] = useState<Hit | null>(null);
  const [searching, setSearching] = useState(false);
  const needsTyping = typing || !!error || (where != null && where.accuracy > ROUGH_M);

  async function find() {
    if (query.trim().length < 2) return;
    setSearching(true);
    try {
      setHits(await archive.places(query.trim()));
    } catch {
      setHits([]);
    } finally {
      setSearching(false);
    }
  }

  function reset() {
    setFile(null);
    setNote("");
    setTyping(false);
    setQuery("");
    setHits(null);
    setChosen(null);
    setError(null);
  }

  function start() {
    setError(null);
    fix.current = locate(); // ask for the position right away, while the camera opens
    fix.current.then(setWhere).catch((e: Error) => setError(e.message));
    input.current?.click();
  }

  async function save() {
    const here = chosen ?? (needsTyping ? null : where);
    if (!file || !here) return;
    setBusy(true);
    try {
      const r = await archive.upload(file, { latitude: here.latitude, longitude: here.longitude });
      const id = r.added[0];
      if (!id) throw new Error(r.skipped[0] ?? "Upload failed");
      if (note.trim()) await archive.patch(id, { note: note.trim() });
      reset();
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
          {chosen ? (
            <p className="text-muted-foreground">📍 {chosen.name}</p>
          ) : (
            <p className="text-muted-foreground">
              {where
                ? `Location captured (±${Math.round(where.accuracy)} m)${where.accuracy > ROUGH_M ? " - too rough; type where you are below" : ""}`
                : !error && "Getting your location…"}
            </p>
          )}
          {needsTyping ? (
            <div className="space-y-2 rounded-md bg-muted/50 p-2">
              <p className="font-medium">Where are you?</p>
              <div className="flex gap-2">
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && void find()}
                  placeholder="Hotel, restaurant, street or village, e.g. “Hotel Sole Malcesine”"
                  className="min-w-0 flex-1 rounded-md border bg-background p-2 outline-none focus-visible:ring-2 focus-visible:ring-ring"
                />
                <Button variant="outline" onClick={() => void find()} disabled={searching}>
                  {searching ? <Loader2 className="animate-spin" /> : <Search />} Find
                </Button>
              </div>
              {hits && hits.length === 0 && <p className="text-muted-foreground">Nothing found. Try adding the town name.</p>}
              {hits && hits.length > 0 && (
                <ul className="space-y-1">
                  {hits.map((h) => (
                    <li key={`${h.latitude},${h.longitude}`}>
                      <button
                        type="button"
                        onClick={() => setChosen(h)}
                        className={`w-full rounded-md border p-2 text-left ${chosen === h ? "border-primary bg-primary/10" : "bg-background"}`}
                      >
                        {h.name}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ) : (
            <button type="button" className="text-xs underline text-muted-foreground" onClick={() => setTyping(true)}>
              Wrong place? Type where you are instead
            </button>
          )}
          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="What is this? e.g. “Aunt Mary's wedding, Lake Como”"
            className="w-full rounded-md border bg-background p-2 outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
          <div className="flex gap-2">
            <Button onClick={() => void save()} disabled={busy || !(chosen || (where && !needsTyping))}>
              {busy ? <Loader2 className="animate-spin" /> : <MapPinned />} Save
            </Button>
            <Button variant="ghost" onClick={reset} disabled={busy}>
              Cancel
            </Button>
          </div>
        </div>
      )}
      {error && !chosen && <p className="text-muted-foreground">{error} You can type where you are instead.</p>}
    </div>
  );
}
