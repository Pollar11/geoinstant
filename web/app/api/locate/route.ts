import { allow, checkUpload, clientIp, INFERENCE_URL, json, unavailable, upstreamHeaders } from "@/lib/server/upstream";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 30;

/** Streams the inference service's SSE progress events straight through to the browser. */
export async function POST(req: Request): Promise<Response> {
  const bad = checkUpload(req);
  if (bad) return bad;
  if (!allow(`locate:${clientIp(req)}`)) return json({ error: "Too many requests" }, 429);
  if (!req.body) return json({ error: "Empty upload" }, 400);

  let upstream: Response;
  try {
    upstream = await fetch(`${INFERENCE_URL}/v1/locate/stream`, {
      method: "POST",
      body: req.body,
      headers: upstreamHeaders(req, { "Content-Type": req.headers.get("content-type") ?? "application/octet-stream" }),
      signal: req.signal, // user navigates away → stop the upstream work too
      // @ts-expect-error - Node's fetch requires this for streaming request bodies
      duplex: "half",
    });
  } catch {
    return unavailable();
  }
  if (!upstream.ok || !upstream.body) {
    const body = await upstream.text().catch(() => "");
    return new Response(body || JSON.stringify({ error: "Location failed" }), {
      status: upstream.status,
      headers: { "Content-Type": upstream.headers.get("content-type") ?? "application/json", "Cache-Control": "no-store" },
    });
  }
  return new Response(upstream.body, {
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-store, no-transform",
      "X-Accel-Buffering": "no",
    },
  });
}
