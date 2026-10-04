export class LoobError extends Error {
  constructor(message: string) { super(message); }
}

const baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8001";

async function detail(response: Response) {
  const body = await response.json().catch(() => null);
  return body?.detail ?? "Something went wrong. Please try again.";
}

export async function ask(question: string, pageText: string) {
  const response = await fetch(`${baseUrl}/v1/ask`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question, page_text: pageText }) }).catch(() => { throw new LoobError("LOOB cannot reach the local AI service."); });
  if (!response.ok) throw new LoobError(await detail(response));
  return response.json() as Promise<{ answer: string }>;
}

export type CameraScan = {
  accepted: boolean;
  reason: string;
  text: string;
  metrics: Record<string, number | boolean>;
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

export async function makeSpeech(text: string, signal?: AbortSignal) {
  const response = await fetch(`${baseUrl}/v1/speech`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }), signal }).catch((error) => {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new LoobError("LOOB cannot reach the voice service.");
  });
  if (!response.ok) throw new LoobError(await detail(response));
  return response.blob();
}
