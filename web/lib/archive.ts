/** Album (private archive) types and API calls; mirrors inference/geoinstant/archive. */
import { z } from "zod";

import { Investigation, LocateResult, StreetJob, StreetResult } from "./api-types";
import { ApiError } from "./client";
import { downscale, readGps, readTaken } from "./prepare-image";

export const Location = z.object({
  latitude: z.number(),
  longitude: z.number(),
  label: z.string(),
  source: z.enum(["you", "photo", "group"]),
  confidence: z.number(),
  resolution: z.string(),
  via: z.string().nullable(),
});
export type Location = z.infer<typeof Location>;

export const PhotoSummary = z.object({
  id: z.string(),
  filename: z.string(),
  added_at: z.string(),
  status: z.enum(["queued", "analyzing", "done", "error"]),
  error: z.string().nullable(),
  width: z.number(),
  height: z.number(),
  group_id: z.string().nullable(),
  group_name: z.string().nullable(),
  scene: z.string().nullable(),
  era: z.string().nullable(),
  location: Location.nullable(),
  lead: z.string().nullable().optional(),
  searching: z.string().nullable().optional(),
  taken_at: z.string().nullable().optional(),
});
export type PhotoSummary = z.infer<typeof PhotoSummary>;

export { StreetJob, StreetMatch, StreetResult } from "./api-types";

export const PhotoDetail = PhotoSummary.extend({
  note: z.string().nullable(),
  result: LocateResult.nullable(),
  investigation: Investigation.nullable().optional(),
  streetmatch: StreetResult.nullable().optional(),
  skyline: z
    .object({ pinned: z.boolean(), message: z.string(), confidence: z.number(), status: z.string() })
    .passthrough()
    .nullable()
    .optional(),
});
export type PhotoDetail = z.infer<typeof PhotoDetail>;

export const Group = z.object({ id: z.string(), name: z.string(), photo_ids: z.array(z.string()), location: Location.nullable() });
export type Group = z.infer<typeof Group>;

export const imageUrl = (id: string, size: "thumb" | "full" = "full") => `/api/archive/photos/${id}/image?size=${size}`;

async function call<T>(path: string, schema: z.ZodType<T> | null, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api/archive/${path}`, init);
  if (!res.ok) {
    let msg = `Request failed (${res.status})`;
    try {
      const b = await res.json();
      msg = b.detail ?? b.error ?? msg;
    } catch {
      /* empty */
    }
    throw new ApiError(msg, res.status);
  }
  return schema ? schema.parse(await res.json()) : (undefined as T);
}

const jsonInit = (method: string, body: unknown): RequestInit => ({
  method,
  body: JSON.stringify(body),
  headers: { "Content-Type": "application/json" },
});

export const archive = {
  list: () => call("photos", z.array(PhotoSummary)),
  get: (id: string) => call(`photos/${id}`, PhotoDetail),
  patch: (id: string, body: Record<string, unknown>) => call(`photos/${id}`, PhotoDetail, jsonInit("PATCH", body)),
  remove: (id: string) => call(`photos/${id}`, null, { method: "DELETE" }),
  reanalyze: (id: string) => call(`photos/${id}/reanalyze`, null, { method: "POST" }),
  groups: () => call("groups", z.array(Group)),
  createGroup: (name: string, photoIds: string[]) => call("groups", Group, jsonInit("POST", { name, photo_ids: photoIds })),
  deleteGroup: (id: string) => call(`groups/${id}`, null, { method: "DELETE" }),
  streetMatch: (id: string, bbox: [number, number, number, number]) => call(`photos/${id}/streetmatch`, StreetJob, jsonInit("POST", { bbox })),
  streetJob: (jobId: string) => call(`streetmatch/${jobId}`, StreetJob),
  places: (q: string) =>
    call(`places?q=${encodeURIComponent(q)}`, z.array(z.object({ name: z.string(), latitude: z.number(), longitude: z.number() }))),

  /** Shrink on the device (≤ 2048 px) and send GPS read from the original (or the phone's live position) alongside. */
  async upload(file: File, here?: { latitude: number; longitude: number }): Promise<{ added: string[]; skipped: string[] }> {
    const gps = here ?? (await readGps(file));
    // Shrinking strips EXIF, so the camera time goes alongside (local clock, like cameras write it).
    const taken = here ? localNow() : await readTaken(file);
    const small = await downscale(file, 2048);
    const form = new FormData();
    form.append("files", small?.blob ?? file, file.name);
    form.append("gps", JSON.stringify([{ lat: gps?.latitude, lon: gps?.longitude, taken }]));
    return call("photos", z.object({ added: z.array(z.string()), skipped: z.array(z.string()) }), { method: "POST", body: form });
  },
};

function localNow(): string {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

export async function login(password: string): Promise<void> {
  const res = await fetch("/api/auth/login", jsonInit("POST", { password }));
  if (!res.ok) throw new ApiError((await res.json().catch(() => ({})))?.error ?? "Sign-in failed", res.status);
}

export async function logout(): Promise<void> {
  await fetch("/api/auth/logout", { method: "POST" });
}
