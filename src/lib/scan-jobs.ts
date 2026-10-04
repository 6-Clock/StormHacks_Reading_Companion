"use client";

import { useCallback, useEffect, useEffectEvent, useRef, useState } from "react";
import { createScanJob, getScanJob, latestScanJob, LoobError, scanIsActive, scanJobAction, type ScanJob } from "@/lib/api";

export function useScanJobs(onComplete: (job: ScanJob) => void) {
  const [job, setJob] = useState<ScanJob | null>(null);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [action, setAction] = useState<"starting" | "capture" | "cancel" | null>(null);
  const working = useRef(false);
  const scannerActive = useRef(false);
  const generation = useRef(0);
  const notifyComplete = useEffectEvent(onComplete);

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const request = new AbortController();
    // Retain only the current result; no camera images in subsequent status polls.
    let completed: ScanJob | null = null;
    const delivered = new Set<string>();
    async function poll() {
      const version = generation.current;
      let delay = 1_000;
      try {
        let next = await latestScanJob(request.signal);
        if (next.job_id && !scanIsActive(next)) {
          if (completed?.job_id !== next.job_id) {
            const result = await getScanJob(next.job_id, request.signal);
            const latest = await latestScanJob(request.signal);
            // A more recent capture takes precedence over an older result.
            next = latest.job_id === result.job_id ? result : latest;
            // A terminal summary for a different job still needs its own result fetch.
            if (next.job_id === result.job_id && !scanIsActive(result)) completed = result;
          } else next = completed;
        }
        if (!stopped && !working.current && version === generation.current) {
          scannerActive.current = scanIsActive(next);
          setJob(next);
          setConnected(true);
          setError(null);
          if (next.job_id && completed?.job_id === next.job_id && !scanIsActive(next) && !delivered.has(next.job_id)) {
            notifyComplete(next);
            delivered.add(next.job_id);
            if (delivered.size > 64) delivered.delete(delivered.values().next().value!);
          }
        }
        delay = scanIsActive(next) ? 500 : 1_000;
      } catch (failure) {
        if (!stopped && version === generation.current) {
          setConnected(false);
          setError(failure instanceof Error ? failure.message : "Scan status unavailable.");
        }
        delay = 2_000;
      } finally {
        if (!stopped) timer = setTimeout(poll, delay);
      }
    }
    void poll();
    return () => { stopped = true; clearTimeout(timer); request.abort(); };
  }, []);

  const run = useCallback(async (kind: "starting" | "capture" | "cancel", perform: () => Promise<ScanJob>) => {
    // Drop new starts immediately, including calls from an older render. Never replay them.
    if (working.current || (kind === "starting" && scannerActive.current)) return;
    working.current = true;
    generation.current += 1;
    setAction(kind);
    setError(null);
    try {
      const next = await perform();
      generation.current += 1;
      scannerActive.current = scanIsActive(next);
      setJob(next);
      setConnected(true);
      return next;
    } catch (failure) {
      if (kind === "starting" && failure instanceof LoobError && failure.status === 409) {
        // Another client/real blink won the scan slot. Poll its progress without retrying.
        scannerActive.current = true;
        setConnected(true);
        return;
      }
      const message = failure instanceof Error ? failure.message : "Scan request failed.";
      setError(message);
      if (!(failure instanceof LoobError) || !failure.status) setConnected(false);
      throw failure;
    } finally {
      working.current = false;
      setAction(null);
    }
  }, []);

  return {
    job, connected, error, action,
    active: scanIsActive(job),
    start: (camera: number, source: "manual" | "test", eyeCamera?: number) => run("starting", () => createScanJob(camera, source, eyeCamera)),
    capture: () => job?.job_id ? run("capture", () => scanJobAction(job.job_id!, "capture")) : Promise.resolve(undefined),
    cancel: () => job?.job_id ? run("cancel", () => scanJobAction(job.job_id!, "cancel")) : Promise.resolve(undefined),
  };
}
