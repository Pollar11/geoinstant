import { allow, clientIp, INFERENCE_URL, json, MAX_UPLOAD_BYTES, unavailable, upstreamHeaders } from "@/lib/server/upstream";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 300; // an investigation can take a couple of minutes

export async function POST(req: Request): Promise<Response> {
  const type = req.headers.get("content-type") ?? "";
  if (!type.startsWith("multipart/form-data")) return json({ error: "Expected multipart/form-data" }, 415);
  if (Number(req.headers.get("content-length") ?? 0) > MAX_UPLOAD_BYTES) return json({ error: "Image is too large" }, 413);
  if (!allow(`investigate:${clientIp(req)}`, 6, 3)) return json({ error: "Too many requests" }, 429);
  let upstream: Response;
  try {
    upstream = await fetch(`${INFERENCE_URL}/v1/investigate`, {
      method: "POST",
      body: req.body,
      headers: upstreamHeaders(req, { "Content-Type": type }),
      signal: req.signal,
      // @ts-expect-error - Node's fetch requires this for streaming request bodies
      duplex: "half",
    });
  } catch {
    return unavailable();
  }
  if (!upstream.ok || !upstream.body) {
    const body = await upstream.text().catch(() => "");
    return new Response(body || JSON.stringify({ error: "Investigation failed" }), { status: upstream.status, headers: { "Content-Type": "application/json" } });
  }
  return new Response(upstream.body, { headers: { "Content-Type": "text/event-stream; charset=utf-8", "Cache-Control": "no-store, no-transform", "X-Accel-Buffering": "no" } });
}
