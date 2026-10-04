export type LiveEvent = { type: string; [key: string]: unknown };
export type TranscriptFragment = {
  id: string;
  sessionId: string;
  speaker: "user" | "assistant";
  text: string;
  startMs: number;
  endMs: number;
};
export type ScanResult = {
  task: "scan_page";
  job_id: string;
  accepted: boolean;
  text: string;
  reason: string;
};
export type ToolCall = { call_id: string; name: string; arguments: string };

export function backendStreamKey(envelope: LiveEvent) {
  const event = envelope.event as LiveEvent | undefined;
  const response = event?.response as { id?: string } | undefined;
  return String(envelope.delegation_id ?? envelope.response_id ?? event?.response_id ?? response?.id ?? "");
}

export function transcriptFragment(event: LiveEvent, sessionId: string): TranscriptFragment | null {
  if (event.type !== "session.input_transcript.delta" && event.type !== "session.output_transcript.delta") return null;
  if (typeof event.event_id !== "string" || typeof event.delta !== "string" ||
      typeof event.start_ms !== "number" || typeof event.end_ms !== "number") return null;
  return {
    id: event.event_id, sessionId,
    speaker: event.type === "session.input_transcript.delta" ? "user" : "assistant",
    text: event.delta, startMs: event.start_ms, endMs: event.end_ms,
  };
}

export function appendTranscript(fragments: TranscriptFragment[], fragment: TranscriptFragment) {
  if (fragments.some((value) => value.sessionId === fragment.sessionId && value.id === fragment.id)) return fragments;
  const result = [...fragments];
  const next = result.findIndex((value) => value.sessionId === fragment.sessionId && value.startMs > fragment.startMs);
  if (next >= 0) result.splice(next, 0, fragment);
  else {
    const last = result.map((value) => value.sessionId).lastIndexOf(fragment.sessionId);
    result.splice(last < 0 ? result.length : last + 1, 0, fragment);
  }
  return result;
}

export function parseScanResult(text: string, jobId: string): ScanResult | null {
  let value: Partial<ScanResult>;
  try { value = JSON.parse(text.replace(/^\s*```(?:json)?\s*|\s*```\s*$/g, "")); }
  catch { return null; }
  if (!value || value.task !== "scan_page" || value.job_id !== jobId ||
      typeof value.accepted !== "boolean" || typeof value.text !== "string" || typeof value.reason !== "string") return null;
  if (value.accepted && !value.text.trim()) return null;
  return value as ScanResult;
}

export function backendMessage(content: unknown[]) {
  return { type: "response.item.create", item: { type: "message", role: "user", content } };
}

export function pageMessage(pageId: string, pageText: string) {
  return backendMessage([{ type: "input_text", text: JSON.stringify({ task: "page_context", page_id: pageId, page_text: pageText }) }]);
}

// Live clears lifecycle output snapshots; content comes from granular Responses events.
export class BackendOutputs {
  private streams = new Map<string, Map<string, string>>();
  private itemStreams = new Map<string, string>();
  private aliases = new Map<string, string>();
  private handledCalls = new Set<string>();

  consume(envelope: LiveEvent): { streamKey?: string; text?: string; call?: ToolCall; error?: string } {
    const bindAlias = (responseId: string, delegationId: string) => {
      this.aliases.set(responseId, delegationId);
      const previous = this.streams.get(responseId);
      if (previous && responseId !== delegationId) {
        const merged = this.streams.get(delegationId) ?? new Map<string, string>();
        previous.forEach((text, part) => merged.set(part, text));
        this.streams.set(delegationId, merged);
        this.streams.delete(responseId);
      }
      for (const [id, stream] of this.itemStreams) if (stream === responseId) this.itemStreams.set(id, delegationId);
    };
    if (envelope.type === "session.delegation.created") {
      const delegation = envelope.delegation as { id?: string; response_id?: string } | undefined;
      if (delegation?.id && delegation.response_id) bindAlias(delegation.response_id, delegation.id);
      return { streamKey: delegation?.id };
    }
    if (envelope.type !== "response.event" || !envelope.event || typeof envelope.event !== "object") return {};
    const event = envelope.event as LiveEvent;
    const response = event.response as { id?: string } | undefined;
    if (typeof envelope.delegation_id === "string" && response?.id) bindAlias(response.id, envelope.delegation_id);
    const item = event.item as Record<string, unknown> | undefined;
    const itemId = typeof event.item_id === "string" ? event.item_id : typeof item?.id === "string" ? item.id : "";
    const rawKey = backendStreamKey(envelope);
    const key = this.aliases.get(rawKey) || rawKey || this.itemStreams.get(itemId) ||
      (this.streams.size === 1 ? this.streams.keys().next().value! : "");
    if (itemId && key) this.itemStreams.set(itemId, key);
    const texts = this.streams.get(key) ?? new Map<string, string>();
    this.streams.set(key, texts);
    const part = `${String(event.item_id ?? "")}:${String(event.content_index ?? 0)}`;
    if (event.type === "response.output_text.delta" && typeof event.delta === "string") {
      texts.set(part, (texts.get(part) ?? "") + event.delta);
    } else if (event.type === "response.output_text.done" && typeof event.text === "string") {
      texts.set(part, event.text);
    } else if (event.type === "response.output_item.done" && event.item && typeof event.item === "object") {
      const item = event.item as Record<string, unknown>;
      if (item.type === "function_call" && typeof item.call_id === "string" &&
          typeof item.name === "string" && typeof item.arguments === "string" && !this.handledCalls.has(item.call_id)) {
        this.handledCalls.add(item.call_id);
        return { streamKey: key, call: item as ToolCall };
      }
      if (item.type === "message" && Array.isArray(item.content)) {
        item.content.forEach((content: Record<string, unknown>, index: number) => {
          if (content.type === "output_text" && typeof content.text === "string") texts.set(`${String(item.id ?? event.item_id ?? "")}:${index}`, content.text);
        });
      }
    } else if (event.type === "response.completed") {
      this.streams.delete(key);
      for (const [id, stream] of this.itemStreams) if (stream === key) this.itemStreams.delete(id);
      return { streamKey: key, text: [...texts.values()].join("") };
    } else if (event.type === "response.failed" || event.type === "response.incomplete") {
      this.streams.delete(key);
      for (const [id, stream] of this.itemStreams) if (stream === key) this.itemStreams.delete(id);
      return { streamKey: key, error: "The reading assistant could not complete its response. Please try again." };
    }
    return { streamKey: key };
  }
}
