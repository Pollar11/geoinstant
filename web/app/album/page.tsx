"use client";

import Link from "next/link";
import { FolderUp, ImageUp, Link2, Loader2, LogOut } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { AlbumMap } from "@/components/album/AlbumMap";
import { Legend, PhotoGrid } from "@/components/album/PhotoGrid";
import { PhotoDetailView } from "@/components/album/PhotoDetailView";
import { StreetMatchPanel } from "@/components/album/StreetMatchPanel";
import { SkylinePanel } from "@/components/SkylinePanel";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { BBox, SkylineResult } from "@/lib/api-types";
import {
  archive,
  imageUrl,
  login,
  logout,
  type Group,
  type PhotoDetail,
  type PhotoSummary,
  type StreetJob,
  type StreetMatch,
  type StreetResult,
} from "@/lib/archive";
import { ApiError, reverse, skylineSearch } from "@/lib/client";
import { formatCoord } from "@/lib/format";
import { ACCEPT } from "@/lib/prepare-image";
import { cn } from "@/lib/utils";

type Filter = "all" | "unlocated" | "located";

export default function Album() {
  const [auth, setAuth] = useState<"unknown" | "in" | "out" | "disabled">("unknown");
  const [photos, setPhotos] = useState<PhotoSummary[]>([]);
  const [groups, setGroups] = useState<Group[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [openId, setOpenId] = useState<string | null>(null);
  const [detail, setDetail] = useState<PhotoDetail | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [upload, setUpload] = useState<{ done: number; total: number; skipped: string[] } | null>(null);
  const [picking, setPicking] = useState(false);
  const [bounds, setBounds] = useState<BBox | null>(null);
  const [error, setError] = useState<string | null>(null);
  // skyline tool (per open photo)
  const [tracing, setTracing] = useState(false);
  const [trace, setTrace] = useState<[number, number][]>([]);
  const [sky, setSky] = useState<SkylineResult | null>(null);
  const [skyBusy, setSkyBusy] = useState(false);
  const [skyError, setSkyError] = useState<string | null>(null);
  const [skySel, setSkySel] = useState(0);
  // street match (per open photo)
  const [smJob, setSmJob] = useState<StreetJob | null>(null);
  const [smResult, setSmResult] = useState<StreetResult | null>(null);
  const [smError, setSmError] = useState<string | null>(null);
  const [smSel, setSmSel] = useState(0);

  const refresh = useCallback(async () => {
    try {
      const [ps, gs] = await Promise.all([archive.list(), archive.groups()]);
      setPhotos(ps);
      setGroups(gs);
      setAuth("in");
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) setAuth("out");
      else if (e instanceof ApiError && e.status === 404) setAuth("disabled");
      else setError(e instanceof Error ? e.message : "Could not load the album");
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Poll while photos are being analysed.
  const busy = photos.some((p) => p.status === "queued" || p.status === "analyzing" || p.searching);
  useEffect(() => {
    if (!busy && !upload) return;
    const id = setInterval(() => void refresh(), 3000);
    return () => clearInterval(id);
  }, [busy, upload, refresh]);

  const loadDetail = useCallback(async (id: string) => setDetail(await archive.get(id)), []);
  useEffect(() => {
    if (openId) void loadDetail(openId);
  }, [openId, loadDetail, photos]);

  const open = (id: string | null) => {
    setOpenId(id);
    setDetail(null);
    setPicking(false);
    setTracing(false);
    setTrace([]);
    setSky(null);
    setSkyError(null);
    setSmJob(null);
    setSmResult(null);
    setSmError(null);
    setSmSel(0);
  };

  // Poll a running street match; when it finishes, reload the photo (a verified match pins it).
  const smRunning = smJob != null && smJob.status !== "done" && smJob.status !== "error";
  const smJobId = smJob?.id;
  useEffect(() => {
    if (!smRunning || !smJobId) return;
    const t = setInterval(async () => {
      try {
        const j = await archive.streetJob(smJobId);
        setSmJob(j);
        if (j.status === "error") setSmError(j.message);
        if (j.status === "done") {
          setSmResult(j.result);
          setSmSel(0);
          void refresh();
        }
      } catch (e) {
        setSmError(e instanceof Error ? e.message : "Street match failed");
        setSmJob(null);
      }
    }, 1000);
    return () => clearInterval(t);
  }, [smRunning, smJobId, refresh]);

  async function searchStreet() {
    if (!openId || !bounds) return;
    setSmError(null);
    try {
      setSmJob(await archive.streetMatch(openId, bounds));
    } catch (e) {
      setSmError(e instanceof Error ? e.message : "Street match failed");
    }
  }

  async function pinStreetSpot(m: StreetMatch) {
    const house = street?.best?.image_id === m.image_id && street.building?.in_view ? street.building.address : null;
    const place = house ? null : await reverse(m.latitude, m.longitude).catch(() => null);
    const label = house ?? place?.display_name ?? formatCoord(m.latitude, m.longitude, 5);
    await patch({ user_lat: m.latitude, user_lon: m.longitude, user_label: label });
  }

  async function addFiles(list: FileList | null) {
    const files = Array.from(list ?? []).filter((f) => f.type.startsWith("image/") || /\.(heic|heif)$/i.test(f.name));
    if (!files.length) return;
    const state = { done: 0, total: files.length, skipped: [] as string[] };
    setUpload({ ...state });
    let next = 0;
    const worker = async () => {
      while (next < files.length) {
        const f = files[next++]!;
        try {
          const r = await archive.upload(f);
          state.skipped.push(...r.skipped);
        } catch (e) {
          state.skipped.push(`${f.name}: ${e instanceof Error ? e.message : "failed"}`);
        }
        state.done++;
        setUpload({ ...state });
      }
    };
    await Promise.all([worker(), worker(), worker()]);
    await refresh();
    setUpload(state.skipped.length ? { ...state } : null);
  }

  async function patch(body: Record<string, unknown>) {
    if (!openId) return;
    try {
      setDetail(await archive.patch(openId, body));
      void refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    }
  }

  async function onPick([lat, lon]: [number, number]) {
    setPicking(false);
    const place = await reverse(lat, lon).catch(() => null);
    await patch({ user_lat: lat, user_lon: lon, user_label: place?.display_name ?? formatCoord(lat, lon, 4) });
  }

  async function linkSelected() {
    const name = window.prompt("Name this place or event (e.g. “Summer 1974, grandma's village”)");
    if (!name) return;
    await archive.createGroup(name, [...selected]);
    setSelected(new Set());
    await refresh();
  }

  async function searchSkyline() {
    if (!openId || !bounds) return;
    setSkyBusy(true);
    setSkyError(null);
    setTracing(false);
    try {
      const blob = await (await fetch(imageUrl(openId))).blob();
      setSky(await skylineSearch(blob, bounds, trace.length >= 3 ? trace : null));
      setSkySel(0);
    } catch (e) {
      setSkyError(e instanceof Error ? e.message : "Skyline search failed");
    } finally {
      setSkyBusy(false);
    }
  }

  const shown = useMemo(
    () => photos.filter((p) => filter === "all" || (filter === "located" ? p.location : !p.location)),
    [photos, filter],
  );
  const street = smResult ?? detail?.streetmatch ?? null;
  // Street candidates (camera position + direction) take over the map from skyline cones.
  const views = useMemo(
    () =>
      street
        ? street.candidates.map((c) => ({ latitude: c.latitude, longitude: c.longitude, azimuth: c.heading, fov: 60, km: 0.06 }))
        : (sky?.candidates ?? []).map((c) => ({ latitude: c.latitude, longitude: c.longitude, azimuth: c.azimuth_deg, fov: c.fov_deg })),
    [sky, street],
  );
  const skylineLine = useMemo(() => sky?.profile.filter((q) => q[2] > 0).map((q) => [q[0], q[1]] as [number, number]), [sky]);
  const located = photos.filter((p) => p.location).length;
  const radiusKm = detail?.location?.source === "photo" && detail.result ? detail.result.uncertainty_radius_m / 1000 : undefined;

  if (auth === "unknown") return <Centered><Loader2 className="animate-spin" /></Centered>;
  if (auth === "disabled")
    return (
      <Centered>
        <p className="max-w-md text-center text-sm text-muted-foreground">
          The album is not enabled. Set ARCHIVE_PASSWORD, SESSION_SECRET and INFERENCE_ARCHIVE_TOKEN on the web app and
          GEOINSTANT_ARCHIVE_TOKEN on the service.
        </p>
      </Centered>
    );
  if (auth === "out") return <Login onDone={() => void refresh()} />;

  return (
    <main className="mx-auto flex min-h-dvh max-w-[96rem] flex-col gap-4 p-3 sm:p-6">
      <header className="flex items-center justify-between gap-3">
        <Link href="/" className="flex items-center gap-2">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/icon.svg" alt="" className="size-8" />
          <span className="text-lg font-semibold">Family album</span>
        </Link>
        <Button
          variant="ghost"
          size="sm"
          onClick={async () => {
            await logout();
            setAuth("out");
          }}
        >
          <LogOut /> Sign out
        </Button>
      </header>
      {error && <p className="rounded-md bg-danger/10 p-2 text-sm text-danger">{error}</p>}

      <div className="grid flex-1 gap-4 lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
        <div className="min-w-0">
          {openId && detail ? (
            <PhotoDetailView
              photo={detail}
              groups={groups}
              picking={picking}
              skyline={skylineLine}
              trace={trace}
              tracing={tracing}
              onTrace={(pt) => setTrace((t) => [...t, pt])}
              onBack={() => open(null)}
              onPickToggle={() => setPicking((x) => !x)}
              onPatch={patch}
              onDelete={async () => {
                if (!window.confirm("Delete this photo from the album?")) return;
                await archive.remove(detail.id);
                open(null);
                await refresh();
              }}
              onReanalyze={async () => {
                await archive.reanalyze(detail.id);
                await refresh();
              }}
              skylinePanel={
                <SkylinePanel
                  bounds={bounds}
                  maxAreaKm2={5000}
                  tracing={tracing}
                  tracePoints={trace.length}
                  busy={skyBusy}
                  result={sky}
                  error={skyError}
                  selected={skySel}
                  onTraceToggle={() => setTracing((t) => !t)}
                  onTraceClear={() => setTrace([])}
                  onSearch={() => void searchSkyline()}
                  onSelect={setSkySel}
                />
              }
              streetPanel={
                <StreetMatchPanel
                  photoUrl={imageUrl(detail.id)}
                  bounds={bounds}
                  job={smJob}
                  result={street}
                  error={smError}
                  selected={smSel}
                  onSearch={() => void searchStreet()}
                  onSelect={setSmSel}
                  onUse={(m) => void pinStreetSpot(m)}
                />
              }
            />
          ) : (
            <div className="flex flex-col gap-4">
              <Uploader onFiles={addFiles} upload={upload} onDismiss={() => setUpload(null)} />
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex gap-1 rounded-lg bg-muted p-1 text-sm">
                  {(["all", "unlocated", "located"] as Filter[]).map((f) => (
                    <button
                      key={f}
                      type="button"
                      onClick={() => setFilter(f)}
                      className={cn("rounded-md px-3 py-1 capitalize", filter === f && "bg-card shadow-sm")}
                    >
                      {f} {f === "all" ? photos.length : f === "located" ? located : photos.length - located}
                    </button>
                  ))}
                </div>
                {selected.size > 0 ? (
                  <div className="flex gap-2">
                    <Button size="sm" onClick={() => void linkSelected()} disabled={selected.size < 2}>
                      <Link2 /> Link {selected.size} as same place/event
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>
                      Clear
                    </Button>
                  </div>
                ) : (
                  <Legend />
                )}
              </div>
              {busy && (
                <p className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="size-4 animate-spin" />
                  Analysing {photos.filter((p) => p.status !== "done" && p.status !== "error").length} photos…
                </p>
              )}
              <PhotoGrid
                photos={shown}
                selected={selected}
                onOpen={open}
                onToggle={(id) =>
                  setSelected((s) => {
                    const n = new Set(s);
                    if (n.has(id)) n.delete(id);
                    else n.add(id);
                    return n;
                  })
                }
              />
            </div>
          )}
        </div>
        <Card className="h-[60dvh] overflow-hidden lg:sticky lg:top-6 lg:h-[calc(100dvh-7rem)]">
          <AlbumMap
            photos={photos}
            selectedId={openId}
            radiusKm={radiusKm}
            picking={picking}
            onPick={(ll) => void onPick(ll)}
            onSelect={open}
            onBounds={setBounds}
            views={views}
            selectedView={street ? smSel : skySel}
          />
        </Card>
      </div>
    </main>
  );
}

function Centered({ children }: { children: React.ReactNode }) {
  return <main className="flex min-h-dvh items-center justify-center p-6">{children}</main>;
}

function Uploader({
  onFiles,
  upload,
  onDismiss,
}: {
  onFiles: (f: FileList | null) => void;
  upload: { done: number; total: number; skipped: string[] } | null;
  onDismiss: () => void;
}) {
  const files = useRef<HTMLInputElement>(null);
  const folder = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        onFiles(e.dataTransfer.files);
      }}
      className={cn("flex flex-wrap items-center gap-3 rounded-lg border-2 border-dashed p-4", over ? "border-primary bg-primary/5" : "bg-card")}
    >
      <input ref={files} type="file" multiple accept={ACCEPT} className="sr-only" onChange={(e) => onFiles(e.target.files)} />
      <input
        ref={folder}
        type="file"
        multiple
        className="sr-only"
        onChange={(e) => onFiles(e.target.files)}
        {...({ webkitdirectory: "", directory: "" } as Record<string, string>)}
      />
      <Button onClick={() => files.current?.click()}>
        <ImageUp /> Add photos
      </Button>
      <Button variant="outline" onClick={() => folder.current?.click()}>
        <FolderUp /> Add a folder
      </Button>
      <span className="text-sm text-muted-foreground">or drop them here</span>
      {upload && (
        <div className="w-full text-sm">
          {upload.done < upload.total ? (
            <p className="flex items-center gap-2">
              <Loader2 className="size-4 animate-spin" /> Uploading {upload.done}/{upload.total}
            </p>
          ) : (
            <div className="text-danger">
              {upload.skipped.length} skipped: {upload.skipped.slice(0, 3).join("; ")}
              <button type="button" className="ml-2 underline" onClick={onDismiss}>
                dismiss
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Login({ onDone }: { onDone: () => void }) {
  const [pw, setPw] = useState("");
  const [err, setErr] = useState<string | null>(null);
  return (
    <Centered>
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>Family album</CardTitle>
        </CardHeader>
        <CardContent>
          <form
            className="space-y-3"
            onSubmit={async (e) => {
              e.preventDefault();
              setErr(null);
              try {
                await login(pw);
                onDone();
              } catch (x) {
                setErr(x instanceof Error ? x.message : "Sign-in failed");
              }
            }}
          >
            <input
              type="password"
              value={pw}
              onChange={(e) => setPw(e.target.value)}
              placeholder="Password"
              autoFocus
              className="w-full rounded-md border bg-background px-3 py-2 outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
            {err && <p className="text-sm text-danger">{err}</p>}
            <Button type="submit" className="w-full">
              Sign in
            </Button>
          </form>
        </CardContent>
      </Card>
    </Centered>
  );
}
