export class LoobError extends Error {
  constructor(message: string, public status?: number) { super(message); }
}

const baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8001";

export async function controlRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  try {
    const response = await fetch(`${baseUrl}${path}`, { ...init, cache: "no-store" });
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      throw new LoobError(typeof body?.detail === "string" ? body.detail : "The local service rejected this request.", response.status);
    }
    return await response.json() as T;
  } catch (error) {
    if (error instanceof LoobError || init.signal?.aborted) throw error;
    throw new LoobError("LOOB cannot reach the local service. Check that FastAPI is running.");
  }
}

export type VoiceSession = {
  session: { id: string };
  transport: { type: "webrtc"; sdp: string };
};

export function createVoiceSession(input: { sdp: string; page_text?: string; page_id?: string }, signal?: AbortSignal) {
  return controlRequest<VoiceSession>("/v1/voice/session", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input), signal,
  });
}

export function uploadVoiceImage(dataUrl: string, signal?: AbortSignal) {
  return controlRequest<{ file_id: string }>("/v1/voice/images", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ data_url: dataUrl }), signal,
  });
}

export type CapturePreview = {
  data_url: string;
  width: number;
  height: number;
  captured_at: number;
  page_detected?: boolean;
};

export type CameraScan = CapturePreview & {
  accepted?: boolean;
  reason?: string;
  text?: string;
};

export type ScanStatus = "idle" | "queued" | "waiting_for_eye_camera" | "opening_camera" | "framing" | "capturing" | "captured" | "cancelling" | "accepted" | "rejected" | "cancelled" | "failed";
export type ScanJob = {
  job_id: string | null;
  trigger_id: string | null;
  source?: "manual" | "test" | "automatic";
  status: ScanStatus;
  message: string;
  updated_at: number;
  eye_camera_index?: number;
  camera_index?: number | null;
  queued_at?: number;
  started_at?: number;
  completed_at?: number;
  duration_ms?: number;
  summary?: { accepted: boolean; reason: string };
  events: Array<{ id: string; time: number; type: string; message: string }>;
  result?: CameraScan;
};

export function scanIsActive(job: ScanJob | null) {
  return !!job && !["idle", "captured", "accepted", "rejected", "cancelled", "failed"].includes(job.status);
}

export function latestScanJob(signal?: AbortSignal) {
  return controlRequest<ScanJob>("/v1/scan-jobs/latest", { signal });
}

export function getScanJob(id: string, signal?: AbortSignal) {
  return controlRequest<ScanJob>(`/v1/scan-jobs/${encodeURIComponent(id)}?include_result=true`, { signal });
}

export type LiveCameraFrame = CapturePreview;
export type LiveCameraPreview = {
  job_id: string; status: ScanStatus; camera_index: number; frame: LiveCameraFrame | null;
};

export function getLiveCameraPreview(id: string, signal?: AbortSignal) {
  return controlRequest<LiveCameraPreview>(`/v1/scan-jobs/${encodeURIComponent(id)}/preview`, { signal });
}

export function createScanJob(cameraIndex: number, source: "manual" | "test", eyeCameraIndex?: number, signal?: AbortSignal) {
  return controlRequest<ScanJob>("/v1/scan-jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    signal,
    body: JSON.stringify({
      trigger_id: crypto.randomUUID(), source, eye_camera_index: eyeCameraIndex, camera_index: cameraIndex,
    }),
  });
}

export function scanJobAction(id: string, action: "capture" | "cancel") {
  return controlRequest<ScanJob>(`/v1/scan-jobs/${encodeURIComponent(id)}/${action}`, { method: "POST" });
}

export function completeScanResult(id: string, result: { accepted: boolean; text: string; reason: string }) {
  return controlRequest<ScanJob>(`/v1/scan-jobs/${encodeURIComponent(id)}/result`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(result),
  });
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
