"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createVoiceSession, uploadVoiceImage, type ScanJob } from "./api";
import { appendTranscript, BackendOutputs, backendMessage, backendStreamKey, pageMessage, parseScanResult, transcriptFragment, type LiveEvent, type ScanResult, type ToolCall, type TranscriptFragment } from "./live-events";

export type { TranscriptFragment, ScanResult } from "./live-events";
export type VoiceStatus = "idle" | "connecting" | "connected" | "closing" | "error";
type Options = {
  pageText: string;
  pageId: string;
  onScanResult: (jobId: string, result: ScanResult) => Promise<void>;
  capturePage: (activeJobId?: string, signal?: AbortSignal) => Promise<ScanJob>;
};
type Connection = {
  peer: RTCPeerConnection;
  channel: RTCDataChannel;
  audio: HTMLAudioElement;
  microphone?: MediaStream;
  abort: AbortController;
  ready: boolean;
  closing: boolean;
  sessionId: string;
  startedAt: number;
  pageKey: string;
  pendingScanId?: string;
  scanCommandIds: Set<string>;
  scanStreamKey?: string;
  outputs: BackendOutputs;
  tools: Map<string, Promise<void>[]>;
  startPromise: Promise<void>;
  resolveStart: () => void;
  rejectStart: (error: Error) => void;
  closeTimer?: ReturnType<typeof setTimeout>;
};

function send(connection: Connection, event: object) {
  if (!connection.ready || connection.closing || connection.channel.readyState !== "open") throw new Error("Start a conversation before sending a request.");
  const eventId = crypto.randomUUID();
  const payload = JSON.stringify({ ...event, event_id: eventId });
  const max = connection.peer.sctp?.maxMessageSize;
  if (max && new TextEncoder().encode(payload).byteLength > max) throw new Error("This input exceeds the connection's message size.");
  connection.channel.send(payload);
  return eventId;
}

async function sendImage(connection: Connection, job: ScanJob) {
  const dataUrl = job.result?.data_url;
  if (!job.job_id || !dataUrl) throw new Error("The camera capture has no image.");
  const text = { type: "input_text", text: JSON.stringify({ task: "scan_page", job_id: job.job_id }) };
  const inline = backendMessage([text, { type: "input_image", image_url: dataUrl, detail: "high" }]);
  const max = connection.peer.sctp?.maxMessageSize || 65_536;
  if (new TextEncoder().encode(JSON.stringify(inline)).byteLength + 100 <= max) return send(connection, inline);
  else {
    const { file_id } = await uploadVoiceImage(dataUrl, connection.abort.signal);
    return send(connection, backendMessage([text, { type: "input_image", file_id, detail: "high" }]));
  }
}

export function useVoiceSession(options: Options) {
  const [status, setStatus] = useState<VoiceStatus>("idle");
  const [error, setError] = useState<string | null>(null);
  const [microphoneMuted, setMicrophoneMuted] = useState(false);
  const [fragments, setFragments] = useState<TranscriptFragment[]>([]);
  const [pendingScanId, setPendingScanId] = useState<string | null>(null);
  const current = useRef<Connection | null>(null);
  const latest = useRef(options);
  useEffect(() => { latest.current = options; }, [options]);

  const cleanup = useCallback((connection: Connection) => {
    if (current.current !== connection) return;
    current.current = null;
    clearTimeout(connection.closeTimer);
    connection.abort.abort();
    connection.audio.muted = true;
    connection.audio.pause();
    connection.audio.srcObject = null;
    connection.microphone?.getTracks().forEach((track) => track.stop());
    connection.channel.close();
    connection.peer.close();
    connection.rejectStart(new Error("Conversation ended before connecting."));
    setPendingScanId(null);
    setMicrophoneMuted(false);
  }, []);

  const fail = useCallback((connection: Connection, cause: unknown) => {
    if (current.current !== connection) return;
    const message = cause instanceof Error ? cause.message : "The voice connection failed.";
    connection.rejectStart(new Error(message));
    cleanup(connection);
    setError(message);
    setStatus("error");
  }, [cleanup]);

  const updatePage = useCallback((connection: Connection) => {
    const { pageId, pageText } = latest.current;
    const key = JSON.stringify([pageId, pageText]);
    if (!connection.ready || connection.closing || connection.pageKey === key) return;
    send(connection, pageMessage(pageId, pageText));
    send(connection, {
      type: "session.thinking.append", delegation_id: null,
      content: "The application's current accepted page is now the latest page_context reference. Use that page for reading and questions; earlier pages are previous references.",
    });
    connection.pageKey = key;
  }, []);

  const executeTool = useCallback(async (connection: Connection, call: ToolCall, streamKey: string) => {
    let output: object;
    try {
      if (call.name !== "capture_page") throw new Error(`Unsupported tool: ${call.name}`);
      const job = await latest.current.capturePage(connection.pendingScanId, connection.abort.signal);
      if (current.current !== connection || connection.closing) return;
      if (job.status !== "captured" || !job.job_id) throw new Error(job.message || "The camera did not capture a page.");
      connection.pendingScanId = job.job_id;
      connection.scanStreamKey = streamKey;
      setPendingScanId(job.job_id);
      connection.scanCommandIds.add(await sendImage(connection, job));
      output = { status: "captured", job_id: job.job_id };
    } catch (cause) {
      output = { status: "failed", reason: cause instanceof Error ? cause.message : "Camera capture failed." };
    }
    if (current.current === connection && !connection.closing) {
      send(connection, { type: "response.item.create", item: { type: "function_call_output", call_id: call.call_id, output: JSON.stringify(output) } });
    }
  }, []);

  const start = useCallback((): Promise<void> => {
    if (current.current) return current.current.closing
      ? Promise.reject(new Error("The previous conversation is still closing."))
      : current.current.startPromise;
    setError(null);
    setStatus("connecting");
    const peer = new RTCPeerConnection();
    const channel = peer.createDataChannel("oai-events");
    const audio = new Audio();
    audio.autoplay = true;
    let resolveStart!: () => void;
    let rejectStart!: (error: Error) => void;
    const startPromise = new Promise<void>((resolve, reject) => { resolveStart = resolve; rejectStart = reject; });
    const connection: Connection = {
      peer, channel, audio, abort: new AbortController(), ready: false, closing: false,
      sessionId: "", startedAt: performance.now(), pageKey: "", outputs: new BackendOutputs(), tools: new Map(), scanCommandIds: new Set(),
      startPromise, resolveStart, rejectStart,
    };
    current.current = connection;
    peer.addEventListener("track", ({ track }) => {
      if (current.current !== connection || connection.closing) return;
      audio.srcObject = new MediaStream([track]);
      void audio.play().catch(() => setError("Audio playback was blocked. Start the conversation again to hear LOOB."));
    });
    peer.addEventListener("connectionstatechange", () => {
      if (peer.connectionState === "failed") fail(connection, new Error("The voice connection was lost."));
    });
    channel.addEventListener("close", () => {
      if (current.current === connection) fail(connection, new Error("The voice connection closed before session finalization."));
    });
    channel.addEventListener("message", ({ data }) => {
      if (current.current !== connection) return;
      let event: LiveEvent;
      try { event = JSON.parse(data); } catch { return; }
      if (event.type === "session.closed") {
        cleanup(connection);
        setStatus("idle");
        return;
      }
      if (connection.closing) return;
      if (event.type === "session.started") {
        connection.ready = true;
        connection.startedAt = performance.now();
        const session = event.session as { id?: string } | undefined;
        connection.sessionId = session?.id ?? connection.sessionId;
        setStatus("connected");
        try { updatePage(connection); connection.resolveStart(); } catch (cause) { fail(connection, cause); }
        return;
      }
      if (event.type === "error") {
        const detail = event.error as { message?: string; client_event_id?: string } | undefined;
        if (!connection.ready) { fail(connection, new Error(detail?.message ?? "The voice session could not start.")); return; }
        const rejectedCommand = detail?.client_event_id ?? event.client_event_id;
        if (!rejectedCommand || connection.scanCommandIds.has(String(rejectedCommand))) {
          connection.pendingScanId = undefined;
          setPendingScanId(null);
        }
        setError(detail?.message ?? "The reading assistant rejected a request.");
        return;
      }
      if (event.type === "session.delegation.created" && connection.scanCommandIds.has(String(event.client_event_id))) {
        const delegation = event.delegation as { id?: string; response_id?: string } | undefined;
        connection.scanStreamKey = delegation?.id ?? delegation?.response_id;
      }
      const fragment = transcriptFragment(event, connection.sessionId);
      if (fragment) setFragments((previous) => appendTranscript(previous, fragment));
      const result = connection.outputs.consume(event);
      const delegationId = result.streamKey ?? backendStreamKey(event);
      if (event.type === "response.event" && connection.scanCommandIds.has(String(event.client_event_id))) connection.scanStreamKey = delegationId;
      if (result.call) {
        const calls = connection.tools.get(delegationId) ?? [];
        calls.push(executeTool(connection, result.call, delegationId));
        connection.tools.set(delegationId, calls);
      }
      if (result.error) {
        connection.pendingScanId = undefined;
        setPendingScanId(null);
        setError(result.error);
      }
      if (result.text !== undefined) {
        const calls = connection.tools.get(delegationId);
        if (calls) {
          connection.tools.delete(delegationId);
          void Promise.all(calls).then(() => {
            if (current.current === connection && !connection.closing) send(connection, { type: "response.create" });
          }).catch((cause) => setError(cause instanceof Error ? cause.message : "Camera capture failed."));
        } else if (connection.pendingScanId) {
          const scan = parseScanResult(result.text, connection.pendingScanId);
          if (scan) {
            connection.pendingScanId = undefined;
            setPendingScanId(null);
            void latest.current.onScanResult(scan.job_id, scan).catch((cause) => setError(cause instanceof Error ? cause.message : "The scan result could not be saved."));
          } else if (connection.scanStreamKey === delegationId || !result.text.trim() || /"task"\s*:\s*"scan_page"/.test(result.text)) {
            connection.pendingScanId = undefined;
            setPendingScanId(null);
            setError("The assistant returned an invalid scan result. The previous page is unchanged; retry the captured page.");
          }
        }
      }
    });
    void (async () => {
      const microphone = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (current.current !== connection || connection.abort.signal.aborted) {
        microphone.getTracks().forEach((track) => track.stop());
        return;
      }
      connection.microphone = microphone;
      microphone.getAudioTracks().forEach((track) => peer.addTrack(track, microphone));
      await peer.setLocalDescription(await peer.createOffer());
      if (peer.iceGatheringState !== "complete") {
        await new Promise<void>((resolve, reject) => {
          const onState = () => {
            if (peer.iceGatheringState === "complete") { remove(); resolve(); }
          };
          const onAbort = () => { remove(); reject(new Error("Conversation cancelled.")); };
          const remove = () => {
            peer.removeEventListener("icegatheringstatechange", onState);
            connection.abort.signal.removeEventListener("abort", onAbort);
          };
          peer.addEventListener("icegatheringstatechange", onState);
          connection.abort.signal.addEventListener("abort", onAbort, { once: true });
          if (connection.abort.signal.aborted) onAbort(); else onState();
        });
      }
      const sdp = peer.localDescription?.sdp;
      if (!sdp) throw new Error("The browser could not create a voice connection.");
      const { pageId, pageText } = latest.current;
      connection.pageKey = JSON.stringify([pageId, pageText]);
      const result = await createVoiceSession({ sdp, page_id: pageId, page_text: pageText }, connection.abort.signal);
      if (current.current !== connection) return;
      connection.sessionId = result.session.id;
      await peer.setRemoteDescription({ type: "answer", sdp: result.transport.sdp });
    })().catch((cause) => fail(connection, cause));
    return startPromise;
  }, [cleanup, executeTool, fail, updatePage]);

  const end = useCallback(() => {
    const connection = current.current;
    if (!connection || connection.closing) return;
    connection.audio.muted = true;
    connection.audio.pause();
    connection.audio.srcObject = null;
    connection.microphone?.getAudioTracks().forEach((track) => { track.enabled = false; });
    if (!connection.ready || connection.channel.readyState !== "open") {
      cleanup(connection);
      setStatus("idle");
      return;
    }
    send(connection, { type: "session.close" });
    connection.closing = true;
    connection.abort.abort();
    setStatus("closing");
    setPendingScanId(null);
    // Keep the transport alive for session.closed after suppressing local audio.
    connection.closeTimer = setTimeout(() => fail(connection, new Error("Conversation ended, but finalization was not confirmed.")), 15_000);
  }, [cleanup, fail]);

  const sendText = useCallback(async (text: string) => {
    if (!text.trim()) return;
    await start();
    const connection = current.current;
    if (!connection) return;
    send(connection, backendMessage([{ type: "input_text", text }]));
    send(connection, { type: "response.create" });
    const time = performance.now() - connection.startedAt;
    setFragments((previous) => appendTranscript(previous, {
      id: crypto.randomUUID(), sessionId: connection.sessionId, speaker: "user", text, startMs: time, endMs: time,
    }));
  }, [start]);

  const readPage = useCallback(async () => {
    await start();
    const connection = current.current;
    if (!connection) return;
    updatePage(connection);
    send(connection, backendMessage([{ type: "input_text", text: JSON.stringify({ task: "read_page", page_id: latest.current.pageId }) }]));
    send(connection, { type: "response.create" });
  }, [start, updatePage]);

  const submitCapturedPage = useCallback(async (job: ScanJob) => {
    const connection = current.current;
    if (!connection?.ready || connection.closing) throw new Error("Start a conversation to interpret the captured page.");
    if (!job.job_id) throw new Error("The capture has no scan identity.");
    connection.pendingScanId = job.job_id;
    connection.scanStreamKey = undefined;
    connection.scanCommandIds.clear();
    setPendingScanId(job.job_id);
    try {
      connection.scanCommandIds.add(await sendImage(connection, job));
      connection.scanCommandIds.add(send(connection, { type: "response.create" }));
    } catch (cause) {
      connection.pendingScanId = undefined;
      setPendingScanId(null);
      throw cause;
    }
  }, []);

  const toggleMute = useCallback(() => {
    const microphone = current.current?.microphone;
    if (!microphone) return;
    const muted = microphone.getAudioTracks().some((track) => track.enabled);
    microphone.getAudioTracks().forEach((track) => { track.enabled = !muted; });
    setMicrophoneMuted(muted);
  }, []);

  useEffect(() => {
    const connection = current.current;
    if (connection) {
      void Promise.resolve().then(() => updatePage(connection)).catch((cause) => setError(cause instanceof Error ? cause.message : "Page context could not be updated."));
    }
  }, [options.pageId, options.pageText, updatePage]);
  useEffect(() => () => { if (current.current) cleanup(current.current); }, [cleanup]);

  return { status, error, microphoneMuted, fragments, pendingScanId, start, end, stop: end, toggleMute, readPage, sendText, submitCapturedPage };
}
