import { allow, clientIp, INFERENCE_URL, json, passThrough, unavailable, upstreamHeaders } from "@/lib/server/upstream";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(req: Request): Promise<Response> {
  if (!allow(`nearby:${clientIp(req)}`, 60, 20)) return json({ error: "Too many requests" }, 429);
  const q = new URL(req.url).searchParams;
  const params = new URLSearchParams();
  for (const k of ["lat", "lon", "radius_m", "heading"]) {
    const v = q.get(k);
    if (v !== null && Number.isFinite(Number(v))) params.set(k, v);
  }
  try {
    return await passThrough(await fetch(`${INFERENCE_URL}/v1/nearby?${params}`, { headers: upstreamHeaders(req), signal: req.signal }));
  } catch {
    return unavailable();
  }
}
