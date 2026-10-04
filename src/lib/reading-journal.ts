import type { TranscriptFragment } from "@/lib/live-events";

export function groupTranscriptFragments(fragments: TranscriptFragment[]): TranscriptFragment[] {
  const entries: TranscriptFragment[] = [];
  for (const fragment of fragments) {
    const previous = entries.at(-1);
    if (previous?.sessionId === fragment.sessionId && previous.speaker === fragment.speaker) {
      previous.text += fragment.text;
      previous.endMs = fragment.endMs;
    } else entries.push({ ...fragment });
  }
  return entries;
}
