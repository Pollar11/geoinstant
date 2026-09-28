/** Server-only helpers for the /api proxy routes (API key, client IP, limits). */
import "server-only";

export const INFERENCE_URL = (process.env.INFERENCE_URL ?? "http://localhost:8000").replace(/\/$/, "");
const API_KEY = process.env.INFERENCE_API_KEY ?? "";

export const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;
const ALLOWED_TYPES = /^(image\/(jpeg|png|webp|heic|heif)|application\/octet-stream)$/i;

export function clientIp(req: Request): string {
  // Behind Vercel / most CDNs the first X-Forwarded-For entry is the real client.
  const fwd = req.headers.get("x-forwarded-for")?.split(",")[0]?.trim();
  return fwd || req.headers.get("x-real-ip") || "unknown";
}

export function upstreamHeaders(req: Request, extra: Record<string, string> = {}): Headers {
  const h = new Headers(extra);
  if (API_KEY) h.set("X-API-Key", API_KEY);
  h.set("X-Forwarded-For", clientIp(req));
  return h;
}

export function checkUpload(req: Request): Response | null {
  const type = (req.headers.get("content-type") ?? "").split(";")[0]?.trim() ?? "";
  if (!ALLOWED_TYPES.test(type)) return json({ error: "Send the image as the request body (image/jpeg, png, webp or heic)." }, 415);
  const length = Number(req.headers.get("content-length") ?? 0);
  if (length > MAX_UPLOAD_BYTES) return json({ error: "Image is too large (25 MB max)." }, 413);
  return null;
}

export function json(body: unknown, status = 200): Response {
  return Response.json(body, { status, headers: { "Cache-Control": "no-store" } });
}

/** Tiny per-instance token bucket: sheds obvious floods before they cost a GPU call. */
const buckets = new Map<string, { tokens: number; at: number }>();
export function allow(key: string, perMinute = 30, burst = 10): boolean {
  const now = Date.now();
  const b = buckets.get(key) ?? { tokens: burst, at: now };
  b.tokens = Math.min(burst, b.tokens + ((now - b.at) / 60_000) * perMinute);
  b.at = now;
  const ok = b.tokens >= 1;
  if (ok) b.tokens -= 1;
  buckets.set(key, b);
  if (buckets.size > 50_000) buckets.delete(buckets.keys().next().value as string);
  return ok;
}

export async function passThrough(upstream: Response): Promise<Response> {
  const headers = new Headers({ "Cache-Control": "no-store" });
  const type = upstream.headers.get("content-type");
  if (type) headers.set("Content-Type", type);
  const retry = upstream.headers.get("retry-after");
  if (retry) headers.set("Retry-After", retry);
  return new Response(upstream.body, { status: upstream.status, headers });
}

export function unavailable(): Response {
  return json({ error: "The location service is unavailable. Please try again shortly." }, 502);
}
