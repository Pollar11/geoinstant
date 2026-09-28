import { json } from "@/lib/server/upstream";
import { COOKIE } from "@/lib/server/session";

export const runtime = "nodejs";

export async function POST(): Promise<Response> {
  const res = json({ ok: true });
  res.headers.append("Set-Cookie", `${COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Secure; Max-Age=0`);
  return res;
}
