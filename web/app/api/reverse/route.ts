import { allow, clientIp, INFERENCE_URL, json, passThrough, unavailable, upstreamHeaders } from "@/lib/server/upstream";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(req: Request): Promise<Response> {
  const url = new URL(req.url);
  const lat = Number(url.searchParams.get("lat"));
  const lon = Number(url.searchParams.get("lon"));
  if (!Number.isFinite(lat) || !Number.isFinite(lon) || Math.abs(lat) > 90 || Math.abs(lon) > 180) {
    return json({ error: "lat/lon out of range" }, 400);
  }
  if (!allow(`reverse:${clientIp(req)}`, 120, 30)) return json({ error: "Too many requests" }, 429);
  try {
    return await passThrough(
      await fetch(`${INFERENCE_URL}/v1/reverse?lat=${lat}&lon=${lon}`, { headers: upstreamHeaders(req), signal: req.signal }),
    );
  } catch {
    return unavailable();
  }
}
