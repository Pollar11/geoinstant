import { allow, clientIp, json } from "@/lib/server/upstream";
import { ARCHIVE_ENABLED, checkPassword, COOKIE, newSession } from "@/lib/server/session";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: Request): Promise<Response> {
  if (!ARCHIVE_ENABLED) return json({ error: "The album is not enabled on this server." }, 404);
  if (!allow(`login:${clientIp(req)}`, 5, 5)) return json({ error: "Too many attempts. Wait a minute." }, 429);
  const body = (await req.json().catch(() => null)) as { password?: unknown } | null;
  if (typeof body?.password !== "string" || !checkPassword(body.password)) return json({ error: "Wrong password" }, 401);
  const s = newSession();
  const res = json({ ok: true });
  res.headers.append("Set-Cookie", `${COOKIE}=${s.value}; Path=/; HttpOnly; SameSite=Lax; Secure; Max-Age=${s.maxAge}`);
  return res;
}
