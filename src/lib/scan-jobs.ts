"use client";

import { useCallback, useEffect, useEffectEvent, useRef, useState } from "react";
import { createScanJob, getScanJob, latestScanJob, scanIsActive, scanJobAction, type ScanJob } from "@/lib/api";

export function useScanJobs(onComplete: (job: ScanJob) => void) {
  const [job, setJob] = useState<ScanJob | null>(null);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [action, setAction] = useState<"starting" | "capture" | "cancel" | null>(null);
  const working = useRef(false);
  const generation = useRef(0);
  const notifyComplete = useEffectEvent(onComplete);

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const request = new AbortController();
    let result: ScanJob | null = null;
    let delivered: string | null = null;
    async function poll() {
      const version = generation.current;
      let delay = 1_000;
      try {
        let next = await latestScanJob(request.signal);
        if (next.job_id && !scanIsActive(next)) {
          if (result?.job_id === next.job_id && result.status === next.status) {
            next = result;
          } else {
            const full = await getScanJob(next.job_id, request.signal);
            const latest = await latestScanJob(request.signal);
            next = latest.job_id === full.job_id && latest.status === full.status ? full : latest;
            if (next === full) result = full;
          }
        }
        if (!stopped && !working.current && version === generation.current) {
          setJob(next);
          setConnected(true);
          setError(null);
          const key = `${next.job_id}:${next.status}`;
          if (next.job_id && !scanIsActive(next) && delivered !== key && result === next) {
            delivered = key;
            notifyComplete(next);
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
    if (working.current) return;
    working.current = true;
    generation.current += 1;
    setAction(kind);
    setError(null);
    try {
      const next = await perform();
      generation.current += 1;
      setJob(next);
      setConnected(true);
      return next;
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Scan request failed.");
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
