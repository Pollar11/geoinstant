/** Album login: a signed, expiring cookie (HMAC-SHA256). */
import "server-only";

import { createHmac, timingSafeEqual } from "node:crypto";

export const COOKIE = "gi_session";
const TTL_S = 60 * 60 * 24 * 30;
const SECRET = process.env.SESSION_SECRET ?? "";
export const ARCHIVE_ENABLED = Boolean(process.env.ARCHIVE_PASSWORD && SECRET && process.env.INFERENCE_ARCHIVE_TOKEN);

const sign = (v: string) => createHmac("sha256", SECRET).update(v).digest("base64url");

function safeEqual(a: string, b: string): boolean {
  const x = Buffer.from(a);
  const y = Buffer.from(b);
  return x.length === y.length && timingSafeEqual(x, y);
}

export function checkPassword(password: string): boolean {
  return ARCHIVE_ENABLED && safeEqual(sign(`pw:${password}`), sign(`pw:${process.env.ARCHIVE_PASSWORD}`));
}

export function newSession(): { value: string; maxAge: number } {
  const exp = Math.floor(Date.now() / 1000) + TTL_S;
  return { value: `${exp}.${sign(String(exp))}`, maxAge: TTL_S };
}

export function validSession(cookieHeader: string | null): boolean {
  if (!ARCHIVE_ENABLED || !cookieHeader) return false;
  const raw = cookieHeader
    .split(";")
    .map((c) => c.trim())
    .find((c) => c.startsWith(`${COOKIE}=`))
    ?.slice(COOKIE.length + 1);
  if (!raw) return false;
  const [exp, mac] = decodeURIComponent(raw).split(".");
  if (!exp || !mac || Number(exp) < Date.now() / 1000) return false;
  return safeEqual(mac, sign(exp));
}
