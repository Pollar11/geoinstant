import { allow, clientIp, INFERENCE_URL, json, MAX_UPLOAD_BYTES, passThrough, unavailable, upstreamHeaders } from "@/lib/server/upstream";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 120; // first search in a new area builds its terrain index

export async function POST(req: Request): Promise<Response> {
  const type = req.headers.get("content-type") ?? "";
  if (!type.startsWith("multipart/form-data")) return json({ error: "Expected multipart/form-data" }, 415);
  if (Number(req.headers.get("content-length") ?? 0) > MAX_UPLOAD_BYTES) return json({ error: "Image is too large" }, 413);
  if (!allow(`skyline:${clientIp(req)}`, 10, 4)) return json({ error: "Too many requests" }, 429);
  try {
    return await passThrough(
      await fetch(`${INFERENCE_URL}/v1/skyline`, {
        method: "POST",
        body: req.body,
        headers: upstreamHeaders(req, { "Content-Type": type }),
        signal: req.signal,
        // @ts-expect-error - Node's fetch requires this for streaming request bodies
        duplex: "half",
      }),
    );
  } catch {
    return unavailable();
  }
}
