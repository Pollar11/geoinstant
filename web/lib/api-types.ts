/** API contract (mirrors inference/geoinstant/schemas.py), validated at runtime. */
import { z } from "zod";

export const Resolution = z.enum(["exact", "street", "city", "region", "country", "continent", "world"]);
export type Resolution = z.infer<typeof Resolution>;

export const Place = z.object({
  name: z.string(),
  admin1: z.string(),
  country: z.string(),
  country_code: z.string(),
  continent: z.string(),
  display_name: z.string(),
  distance_km: z.number(),
});
export type Place = z.infer<typeof Place>;

export const HierarchyNode = z.object({
  level: z.enum(["continent", "country", "region", "city"]),
  name: z.string(),
  code: z.string(),
  probability: z.number(),
});
export type HierarchyNode = z.infer<typeof HierarchyNode>;

export const Candidate = z.object({
  latitude: z.number(),
  longitude: z.number(),
  name: z.string(),
  country_code: z.string(),
  probability: z.number(),
});
export type Candidate = z.infer<typeof Candidate>;

export const EvidenceItem = z.object({
  source: z.enum(["exif", "classifier", "retrieval", "cue", "text", "vlm"]),
  label: z.string(),
  detail: z.string(),
  likelihood_ratio: z.number(),
  direction: z.enum(["supports", "contradicts", "neutral"]),
});
export type EvidenceItem = z.infer<typeof EvidenceItem>;

export const RegionBox = z.object({
  box: z.tuple([z.number(), z.number(), z.number(), z.number()]),
  label: z.string(),
  score: z.number(),
  source: z.string(),
});
export type RegionBox = z.infer<typeof RegionBox>;

export const LocateResult = z.object({
  request_id: z.string(),
  stage: z.enum(["partial", "final", "refined"]),
  source: z.enum(["exif", "xmp", "visual"]),
  latitude: z.number(),
  longitude: z.number(),
  uncertainty_radius_m: z.number(),
  confidence: z.number(),
  resolution: Resolution,
  place: Place,
  hierarchy: z.array(HierarchyNode),
  candidates: z.array(Candidate),
  evidence: z.array(EvidenceItem),
  regions: z.array(RegionBox),
  explanation: z.string(),
  captured_at: z.string().nullable().optional(),
  timings_ms: z.record(z.string(), z.number()),
  mode: z.enum(["production", "partial", "dev"]),
  models: z.record(z.string(), z.string()),
  privacy: z.object({ coarsened: z.boolean(), reason: z.string().nullable(), stored: z.boolean() }),
  cached: z.boolean(),
});
export type LocateResult = z.infer<typeof LocateResult>;

export const StageName = z.enum(["upload", "metadata", "decode", "embedding", "retrieval", "detection", "ocr", "fusion", "vlm"]);
export type StageName = z.infer<typeof StageName>;
export const StageStatus = z.enum(["running", "done", "skipped", "timeout", "error"]);
export type StageStatus = z.infer<typeof StageStatus>;

export const LocateEvent = z.discriminatedUnion("type", [
  z.object({ type: z.literal("stage"), stage: StageName, status: StageStatus, ms: z.number().nullable(), detail: z.string() }),
  z.object({ type: z.literal("partial"), result: LocateResult }),
  z.object({ type: z.literal("result"), result: LocateResult }),
  z.object({ type: z.literal("refined"), result: LocateResult }),
  z.object({ type: z.literal("done"), request_id: z.string(), total_ms: z.number() }),
  z.object({ type: z.literal("error"), status: z.number(), message: z.string() }),
]);
export type LocateEvent = z.infer<typeof LocateEvent>;

export const FeedbackRequest = z.object({
  request_id: z.string().min(8).max(64),
  latitude: z.number().min(-90).max(90),
  longitude: z.number().min(-180).max(180),
  place_name: z.string().max(200).nullable().optional(),
  comment: z.string().max(1000).nullable().optional(),
  was_correct: z.boolean().nullable().optional(),
  consent_store_image: z.boolean().default(false),
  image_base64: z.string().nullable().optional(),
});
export type FeedbackRequest = z.infer<typeof FeedbackRequest>;

export const FeedbackResponse = z.object({
  accepted: z.boolean(),
  feedback_id: z.string(),
  stored_image: z.boolean(),
  stored_embedding: z.boolean(),
});

export const SkylineCandidate = z.object({
  latitude: z.number(),
  longitude: z.number(),
  elevation_m: z.number(),
  azimuth_deg: z.number(),
  fov_deg: z.number(),
  fit_error: z.number(),
  match: z.number(),
  place: Place,
});
export type SkylineCandidate = z.infer<typeof SkylineCandidate>;

export type BBox = [number, number, number, number]; // south, west, north, east

export const SkylineResult = z.object({
  status: z.enum(["ok", "too_flat", "no_area", "area_too_large", "no_data"]),
  message: z.string(),
  confidence: z.number(),
  profile: z.array(z.tuple([z.number(), z.number(), z.number()])),
  traced: z.boolean(),
  relief_deg: z.number(),
  search_area: z.tuple([z.number(), z.number(), z.number(), z.number()]).nullable(),
  viewpoints: z.number(),
  spacing_km: z.number(),
  candidates: z.array(SkylineCandidate),
  heat: z.array(z.tuple([z.number(), z.number(), z.number()])),
  timings_ms: z.record(z.string(), z.number()),
});
export type SkylineResult = z.infer<typeof SkylineResult>;
