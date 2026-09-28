import { INFERENCE_URL, json, passThrough, unavailable, upstreamHeaders } from "@/lib/server/upstream";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** Progress / result of a street search. */
export async function GET(req: Request, { params }: { params: Promise<{ id: string }> }): Promise<Response> {
  const { id } = await params;
  if (!/^[a-f0-9]{6,32}$/.test(id)) return json({ error: "Bad id" }, 400);
  try {
    return await passThrough(await fetch(`${INFERENCE_URL}/v1/streetmatch/${id}`, { headers: upstreamHeaders(req), signal: req.signal }));
  } catch {
    return unavailable();
  }
}
