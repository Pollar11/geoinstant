/** Browser-side API calls (all go through this app's /api routes, never to the inference host). */
import {
  FeedbackResponse,
  InvestigateEvent,
  LocateEvent,
  LocateResult,
  Nearby,
  Place,
  SkylineResult,
  type BBox,
  type FeedbackRequest,
} from "./api-types";
import { readSse } from "./sse";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function errorFrom(res: Response): Promise<ApiError> {
  let message = `Request failed (${res.status})`;
  try {
    const body = await res.json();
    if (typeof body?.detail === "string") message = body.detail;
    else if (typeof body?.error === "string") message = body.error;
  } catch {
    /* not JSON */
  }
  if (res.status === 429) message = "Too many requests - please wait a moment and try again.";
  return new ApiError(message, res.status);
}

export async function* locate(blob: Blob, contentType: string, signal: AbortSignal): AsyncGenerator<LocateEvent> {
  const res = await fetch("/api/locate", { method: "POST", body: blob, headers: { "Content-Type": contentType }, signal });
  if (!res.ok || !res.body) throw await errorFrom(res);
  for await (const data of readSse(res.body, signal)) {
    const parsed = LocateEvent.safeParse(JSON.parse(data));
    if (parsed.success) yield parsed.data;
    else console.warn("Ignoring unexpected event", parsed.error.issues);
  }
}

export async function reverse(latitude: number, longitude: number, signal?: AbortSignal): Promise<Place> {
  const res = await fetch(`/api/reverse?lat=${latitude}&lon=${longitude}`, { signal });
  if (!res.ok) throw await errorFrom(res);
  return Place.parse(await res.json());
}

export async function sendFeedback(body: FeedbackRequest) {
  const res = await fetch("/api/feedback", { method: "POST", body: JSON.stringify(body), headers: { "Content-Type": "application/json" } });
  if (!res.ok) throw await errorFrom(res);
  return FeedbackResponse.parse(await res.json());
}

/** The on-device EXIF path produces the same result shape as the server, so the UI has one code path. */
export function gpsResult(latitude: number, longitude: number, place: Place, capturedAt: string | null, ms: number): LocateResult {
  return {
    request_id: `local-${crypto.randomUUID().replace(/-/g, "").slice(0, 24)}`,
    stage: "final",
    source: "exif",
    latitude,
    longitude,
    uncertainty_radius_m: 15,
    confidence: 99,
    resolution: "exact",
    place,
    hierarchy: [
      { level: "continent", name: place.continent, code: "", probability: 0.99 },
      { level: "country", name: place.country, code: place.country_code, probability: 0.99 },
    ],
    candidates: [],
    evidence: [
      {
        source: "exif",
        label: "GPS coordinates embedded in the photo",
        detail: "Read on your device - the photo was not uploaded. Metadata can be edited, so treat it as a claim.",
        likelihood_ratio: 1e6,
        direction: "supports",
      },
    ],
    regions: [],
    explanation: `The photo carries GPS coordinates: ${place.display_name}.`,
    captured_at: capturedAt,
    timings_ms: { total: ms },
    mode: "production",
    models: {},
    privacy: { coarsened: false, reason: null, stored: false },
    cached: false,
  };
}

export async function skylineSearch(image: Blob, bbox: BBox, trace: [number, number][] | null, signal?: AbortSignal) {
  const form = new FormData();
  form.append("image", image, "photo.jpg");
  form.append("bbox", JSON.stringify(bbox));
  if (trace && trace.length >= 3) form.append("trace", JSON.stringify(trace));
  const res = await fetch("/api/skyline", { method: "POST", body: form, signal });
  if (!res.ok) throw await errorFrom(res);
  return SkylineResult.parse(await res.json());
}

export async function* investigate(image: Blob, context: string, signal?: AbortSignal) {
  const form = new FormData();
  form.append("image", image, "photo.jpg");
  if (context.trim()) form.append("context", context.trim());
  const res = await fetch("/api/investigate", { method: "POST", body: form, signal });
  if (!res.ok || !res.body) throw await errorFrom(res);
  for await (const data of readSse(res.body, signal)) {
    const parsed = InvestigateEvent.safeParse(JSON.parse(data));
    if (parsed.success) yield parsed.data;
  }
}

export async function nearby(lat: number, lon: number, heading?: number) {
  const q = new URLSearchParams({ lat: String(lat), lon: String(lon) });
  if (heading != null) q.set("heading", String(Math.round(heading)));
  const res = await fetch(`/api/nearby?${q}`);
  if (!res.ok) throw await errorFrom(res);
  return Nearby.parse(await res.json());
}
