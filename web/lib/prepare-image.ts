/** Client-side prep: read GPS on device; otherwise downscale to 1024 px JPEG (strips metadata). */
import exifr from "exifr";

export const MAX_EDGE = 1024;
export const ACCEPT = "image/jpeg,image/png,image/webp,image/heic,image/heif,.heic,.heif";
export const MAX_BYTES = 25 * 1024 * 1024;

export type PreparedImage =
  | { kind: "gps"; latitude: number; longitude: number; capturedAt: string | null; previewUrl: string; blob: Blob }
  | { kind: "upload"; blob: Blob; contentType: string; previewUrl: string; width: number; height: number };

export class PrepareError extends Error {}

export async function readGps(file: Blob): Promise<{ latitude: number; longitude: number; capturedAt: string | null } | null> {
  try {
    const out = await exifr.parse(file, { gps: true, xmp: true, tiff: true, exif: true, pick: ["latitude", "longitude", "DateTimeOriginal"] });
    const lat = out?.latitude;
    const lon = out?.longitude;
    if (typeof lat !== "number" || typeof lon !== "number" || !Number.isFinite(lat) || !Number.isFinite(lon)) return null;
    if (Math.abs(lat) < 1e-6 && Math.abs(lon) < 1e-6) return null; // "no fix" written as 0,0
    const dt = out?.DateTimeOriginal instanceof Date ? out.DateTimeOriginal.toISOString() : null;
    return { latitude: lat, longitude: lon, capturedAt: dt };
  } catch {
    return null;
  }
}

export function targetSize(width: number, height: number, maxEdge = MAX_EDGE): { width: number; height: number } {
  const scale = Math.min(1, maxEdge / Math.max(width, height));
  return { width: Math.max(1, Math.round(width * scale)), height: Math.max(1, Math.round(height * scale)) };
}

export async function downscale(file: Blob, maxEdge = MAX_EDGE): Promise<{ blob: Blob; width: number; height: number } | null> {
  let bitmap: ImageBitmap;
  try {
    // imageOrientation "from-image" applies the EXIF rotation before we drop the metadata.
    bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
  } catch {
    return null; // e.g. HEIC in Chrome/Firefox
  }
  const { width, height } = targetSize(bitmap.width, bitmap.height, maxEdge);
  let blob: Blob | null = null;
  if (typeof OffscreenCanvas !== "undefined") {
    const canvas = new OffscreenCanvas(width, height);
    canvas.getContext("2d")?.drawImage(bitmap, 0, 0, width, height);
    blob = await canvas.convertToBlob({ type: "image/jpeg", quality: 0.85 });
  } else {
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    canvas.getContext("2d")?.drawImage(bitmap, 0, 0, width, height);
    blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.85));
  }
  bitmap.close();
  return blob ? { blob, width, height } : null;
}

export async function prepareImage(file: File): Promise<PreparedImage> {
  if (file.size > MAX_BYTES) throw new PrepareError("That image is larger than 25 MB.");
  if (file.type && !file.type.startsWith("image/")) throw new PrepareError("Please choose an image file.");

  const gps = await readGps(file);
  const small = await downscale(file);
  const previewUrl = URL.createObjectURL(small?.blob ?? file);
  if (gps) return { kind: "gps", ...gps, previewUrl, blob: small?.blob ?? file };
  if (small) return { kind: "upload", contentType: "image/jpeg", previewUrl, ...small };
  return { kind: "upload", blob: file, contentType: file.type || "application/octet-stream", previewUrl, width: 0, height: 0 };
}

export async function blobToBase64(blob: Blob): Promise<string> {
  const bytes = new Uint8Array(await blob.arrayBuffer());
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}
