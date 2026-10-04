export class LoobError extends Error {
  constructor(message: string, public status?: number) { super(message); }
}

const baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8001";

export type NarrationMood = "neutral" | "warm" | "suspense";
export type StoryEffect = "door_creak" | "footsteps" | "thunder" | "knock";
export type NarrationCue = { paragraph_index: number; sentence_index: number; effect: StoryEffect };
export type NarrationPlan = {
  moods: NarrationMood[];
  sentences: string[][];
  cues: NarrationCue[];
  source: "ai" | "fallback";
};

async function detail(response: Response) {
  const body = await response.json().catch(() => null);
  return body?.detail ?? "Something went wrong. Please try again.";
}

export async function ask(question: string, pageText: string) {
  const response = await fetch(`${baseUrl}/v1/ask`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question, page_text: pageText }) }).catch(() => { throw new LoobError("LOOB cannot reach the local AI service."); });
  if (!response.ok) throw new LoobError(await detail(response));
  return response.json() as Promise<{ answer: string }>;
}

export type CapturePreview = {
  data_url: string;
  width: number;
  height: number;
  words: Array<{ text: string; x: number; y: number; width: number; height: number; confidence: number }>;
  boxes_status: "available" | "unavailable" | "no_words";
};

export type CameraScan = {
  accepted: boolean;
  reason: string;
  text: string;
  metrics: Record<string, number | boolean>;
  capture_preview?: CapturePreview | null;
  openai_review?: { accepted: boolean; reason: string; model: string } | null;
  openai_review_status: "not_requested" | "requested";
  openai_revision?: { accepted: boolean; reason: string; model: string } | null;
  openai_revision_status: "not_requested" | "skipped_high_confidence" | "requested";
};

export type ScanStatus = "idle" | "reserved" | "queued" | "settling" | "waiting_for_eye_camera" | "opening_camera" | "framing" | "capturing" | "transcribing" | "reviewing" | "preview" | "cancelling" | "accepted" | "rejected" | "unchanged" | "cancelled" | "failed" | "timed_out";
export type ScanJob = {
  job_id: string | null;
  trigger_id: string | null;
  source?: "manual" | "test" | "automatic";
  status: ScanStatus;
  message: string;
  updated_at: number;
  eye_camera_index?: number;
  camera_index?: number | null;
  settle_seconds?: number;
  scan_starts_at?: number;
  queued_at?: number;
  started_at?: number;
  completed_at?: number;
  duration_ms?: number;
  summary?: { accepted: boolean; reason: string; word_count: number };
  events: Array<{ id: string; time: number; type: string; message: string }>;
  result?: CameraScan;
};

export function scanIsActive(job: ScanJob | null) {
  return !!job && !["idle", "accepted", "rejected", "unchanged", "cancelled", "failed", "timed_out"].includes(job.status);
}

/** Bound control requests, including reading their response bodies. OCR runs as a job. */
export async function controlRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 5_000);
  const abort = () => controller.abort();
  init.signal?.addEventListener("abort", abort, { once: true });
  if (init.signal?.aborted) controller.abort();
  try {
    const response = await fetch(`${baseUrl}${path}`, { ...init, cache: "no-store", signal: controller.signal });
    if (!response.ok) {
      const message = await detail(response);
      throw new LoobError(typeof message === "string" ? message : "The local service rejected this request.", response.status);
    }
    return await response.json() as T;
  } catch (error) {
    if (error instanceof LoobError) throw error;
    throw new LoobError(controller.signal.aborted
      ? "The local service did not respond in time. Reconnecting to check the scan."
      : "LOOB cannot reach the local service. Check that FastAPI is running.");
  } finally {
    clearTimeout(timer);
    init.signal?.removeEventListener("abort", abort);
  }
}

export function latestScanJob(signal?: AbortSignal) {
  return controlRequest<ScanJob>("/v1/scan-jobs/latest", { signal });
}

export function getScanJob(id: string, signal?: AbortSignal) {
  return controlRequest<ScanJob>(`/v1/scan-jobs/${encodeURIComponent(id)}?include_result=true`, { signal });
}

export type LiveCameraFrame = { data_url: string; width: number; height: number; captured_at: number };
export type LiveCameraPreview = {
  job_id: string; status: ScanStatus; camera_index: number; frame: LiveCameraFrame | null;
};

export function getLiveCameraPreview(id: string, signal?: AbortSignal) {
  return controlRequest<LiveCameraPreview>(`/v1/scan-jobs/${encodeURIComponent(id)}/preview`, { signal });
}

export function createScanJob(cameraIndex: number, source: "manual" | "test", eyeCameraIndex?: number) {
  return controlRequest<ScanJob>("/v1/scan-jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      trigger_id: crypto.randomUUID(), source, eye_camera_index: eyeCameraIndex,
      camera_index: cameraIndex, settle_seconds: source === "test" ? 8 : 0,
    }),
  });
}

export function scanJobAction(id: string, action: "capture" | "cancel") {
  return controlRequest<ScanJob>(`/v1/scan-jobs/${encodeURIComponent(id)}/${action}`, { method: "POST" });
}

export type TrackerSettings = {
  blink_only: boolean;
  revision: string;
  applied_revision: string | null;
  applied_blink_only: boolean | null;
  tracker_connected: boolean;
  eye_camera_index?: number | null;
  camera_pause_job_id?: string | null;
  eye_camera_state?: "released" | "opening" | "open" | "closing" | "error" | null;
};

export function getTrackerSettings(signal?: AbortSignal) {
  return controlRequest<TrackerSettings>("/v1/tracker-settings", { signal });
}

export function setBlinkOnly(blinkOnly: boolean) {
  return controlRequest<TrackerSettings>("/v1/tracker-settings", {
    method: "PATCH", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ blink_only: blinkOnly }),
  });
}

export async function transcribe(audio: Blob) {
  const form = new FormData();
  form.append("audio", audio, "question.webm");
  const response = await fetch(`${baseUrl}/v1/transcribe`, { method: "POST", body: form }).catch(() => { throw new LoobError("LOOB cannot reach the voice service."); });
  if (!response.ok) throw new LoobError(await detail(response));
  return response.json() as Promise<{ text: string; language?: string }>;
}

export async function planNarration(paragraphs: string[], signal?: AbortSignal) {
  const response = await fetch(`${baseUrl}/v1/narration-plan`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ paragraphs }), signal }).catch((error) => {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new LoobError("LOOB cannot prepare the immersive story plan.");
  });
  if (!response.ok) throw new LoobError(await detail(response));
  return response.json() as Promise<NarrationPlan>;
}

export async function makeSpeech(text: string, signal?: AbortSignal, mood: NarrationMood = "neutral") {
  const response = await fetch(`${baseUrl}/v1/speech`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text, mood }), signal }).catch((error) => {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new LoobError("LOOB cannot reach the voice service.");
  });
  if (!response.ok) throw new LoobError(await detail(response));
  return response.blob();
}
