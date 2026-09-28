import { INFERENCE_URL, json, passThrough, unavailable, upstreamHeaders } from "@/lib/server/upstream";
import { validSession } from "@/lib/server/session";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 60;

const TOKEN = process.env.INFERENCE_ARCHIVE_TOKEN ?? "";

/** Signed-in album requests → inference /v1/archive/*, with the archive token added server-side. */
async function proxy(req: Request, { params }: { params: Promise<{ path: string[] }> }): Promise<Response> {
  if (!validSession(req.headers.get("cookie"))) return json({ error: "Not signed in" }, 401);
  const { path } = await params;
  if (path.some((p) => !/^[\w.-]+$/.test(p))) return json({ error: "Bad path" }, 400);
  const url = new URL(req.url);
  const extra: Record<string, string> = { "X-Archive-Token": TOKEN };
  const type = req.headers.get("content-type");
  if (type) extra["Content-Type"] = type;
  const hasBody = req.method !== "GET" && req.method !== "HEAD";
  try {
    const upstream = await fetch(`${INFERENCE_URL}/v1/archive/${path.join("/")}${url.search}`, {
      method: req.method,
      body: hasBody ? req.body : undefined,
      headers: upstreamHeaders(req, extra),
      signal: req.signal,
      // @ts-expect-error - Node's fetch requires this for streaming request bodies
      duplex: "half",
    });
    const res = await passThrough(upstream);
    const cache = upstream.headers.get("cache-control");
    if (cache) res.headers.set("Cache-Control", cache);
    return res;
  } catch {
    return unavailable();
  }
}

export { proxy as DELETE, proxy as GET, proxy as PATCH, proxy as POST };
