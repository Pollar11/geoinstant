import { allow, clientIp, INFERENCE_URL, json, MAX_UPLOAD_BYTES, passThrough, unavailable, upstreamHeaders } from "@/lib/server/upstream";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** Start the exact-spot street search for one photo (needs the lead from its investigation). */
export async function POST(req: Request): Promise<Response> {
  const type = req.headers.get("content-type") ?? "";
  if (!type.startsWith("multipart/form-data")) return json({ error: "Expected multipart/form-data" }, 415);
  if (Number(req.headers.get("content-length") ?? 0) > MAX_UPLOAD_BYTES) return json({ error: "Image is too large" }, 413);
  if (!allow(`streetmatch:${clientIp(req)}`, 4, 2)) return json({ error: "Too many requests" }, 429);
  try {
    return await passThrough(
      await fetch(`${INFERENCE_URL}/v1/streetmatch`, {
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
