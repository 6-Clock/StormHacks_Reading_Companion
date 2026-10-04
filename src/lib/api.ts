export class LoobError extends Error {
  constructor(message: string) { super(message); }
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

export async function scanCamera(cameraIndex: number) {
  const response = await fetch(`${baseUrl}/v1/scan-camera`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ camera_index: cameraIndex, show_preview: true }),
  }).catch(() => { throw new LoobError("LOOB cannot reach the local OCR service."); });
  if (!response.ok) throw new LoobError(await detail(response));
  return response.json() as Promise<CameraScan>;
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
