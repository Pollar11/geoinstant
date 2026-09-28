import { FeedbackRequest } from "@/lib/api-types";
import { allow, clientIp, INFERENCE_URL, json, MAX_UPLOAD_BYTES, passThrough, unavailable, upstreamHeaders } from "@/lib/server/upstream";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: Request): Promise<Response> {
  if (Number(req.headers.get("content-length") ?? 0) > MAX_UPLOAD_BYTES * 1.4) return json({ error: "Too large" }, 413);
  if (!allow(`feedback:${clientIp(req)}`, 20, 5)) return json({ error: "Too many requests" }, 429);
  const parsed = FeedbackRequest.safeParse(await req.json().catch(() => null));
  if (!parsed.success) return json({ error: "Invalid feedback" }, 400);
  const body = parsed.data;
  if (body.image_base64 && !body.consent_store_image) return json({ error: "Image sharing needs consent" }, 400);
  if (body.request_id.startsWith("local-")) {
    // On-device EXIF results never reached the server, so there is nothing to attach feedback to.
    return json({ accepted: false, feedback_id: "", stored_image: false, stored_embedding: false });
  }
  try {
    return await passThrough(
      await fetch(`${INFERENCE_URL}/v1/feedback`, {
        method: "POST",
        body: JSON.stringify(body),
        headers: upstreamHeaders(req, { "Content-Type": "application/json" }),
      }),
    );
  } catch {
    return unavailable();
  }
}
