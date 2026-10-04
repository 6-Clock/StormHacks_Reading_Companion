"use client";

import { useEffect, useState } from "react";

export type DiagnosticEvent = {
  id: string;
  /** Unix timestamp in seconds. */
  time: number;
  type: string;
  message: string;
};

export type EyeDiagnostics = {
  connected: boolean;
  updated_at: number | null;
  camera_index: number | null;
  mode: "STOP" | "READ" | "READY" | "SIGNAL" | null;
  eyes_visible: boolean;
  gaze: { x: number; y: number } | null;
  openness: { left: number; right: number } | null;
  phase: string | null;
  blink_count: number;
  calibrated: boolean | null;
  turns_blocked: boolean | null;
  blink_only: boolean | null;
  look_progress: number;
  capture_fps: number | null;
  inference_fps: number | null;
  frame_age_ms: number | null;
  events: DiagnosticEvent[];
};

export type EyeDiagnosticsState = {
  data: EyeDiagnostics | null;
  status: "connecting" | "connected" | "disconnected" | "paused";
};

const baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8001";

export function useEyeDiagnostics(active: boolean): EyeDiagnosticsState {
  const [state, setState] = useState<EyeDiagnosticsState>({ data: null, status: "connecting" });

  useEffect(() => {
    if (!active) return;

    let stopped = false;
    let nextPoll: ReturnType<typeof setTimeout> | undefined;
    let freshnessTimer: ReturnType<typeof setTimeout> | undefined;
    let request: AbortController | undefined;
    let resumeImmediately = false;

    async function poll() {
      if (stopped || document.hidden || request) return;
      const currentRequest = new AbortController();
      request = currentRequest;
      let retryDelay = 5_000;
      const timeout = setTimeout(() => currentRequest.abort(), 3_000);
      try {
        const response = await fetch(`${baseUrl}/v1/diagnostics/eyes`, {
          cache: "no-store",
          signal: currentRequest.signal,
        });
        if (!response.ok) throw new Error("Diagnostics unavailable");
        const data = await response.json() as EyeDiagnostics;
        if (typeof data.connected !== "boolean" || !Array.isArray(data.events)) {
          throw new Error("Invalid diagnostics");
        }
        const age = typeof data.updated_at === "number" ? Date.now() - data.updated_at * 1_000 : Infinity;
        if (age < -1_000 || age >= 2_000) data.connected = false;
        retryDelay = data.connected ? 500 : 2_000;
        if (!stopped && !document.hidden) {
          clearTimeout(freshnessTimer);
          setState({ data, status: "connected" });
          if (data.connected) {
            // A stalled request must not leave the last eye measurements looking live.
            freshnessTimer = setTimeout(() => setState((current) => current.data?.updated_at === data.updated_at
              ? { ...current, data: { ...current.data, connected: false } }
              : current), Math.max(0, 2_000 - Math.max(0, age)));
          }
        }
      } catch {
        if (!stopped && !document.hidden) {
          clearTimeout(freshnessTimer);
          setState({ data: null, status: "disconnected" });
        }
      } finally {
        clearTimeout(timeout);
        request = undefined;
        // Schedule only after completion so a slow service never piles up requests.
        if (!stopped && !document.hidden) {
          nextPoll = setTimeout(poll, resumeImmediately ? 0 : retryDelay);
          resumeImmediately = false;
        }
      }
    }

    function onVisibilityChange() {
      clearTimeout(nextPoll);
      clearTimeout(freshnessTimer);
      if (document.hidden) {
        resumeImmediately = false;
        request?.abort();
        return;
      }
      setState({ data: null, status: "connecting" });
      // A just-aborted fetch must settle before another request starts.
      if (request) resumeImmediately = true;
      else void poll();
    }

    document.addEventListener("visibilitychange", onVisibilityChange);
    void poll();
    return () => {
      stopped = true;
      clearTimeout(nextPoll);
      clearTimeout(freshnessTimer);
      request?.abort();
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [active]);

  return active ? state : { data: null, status: "paused" };
}
