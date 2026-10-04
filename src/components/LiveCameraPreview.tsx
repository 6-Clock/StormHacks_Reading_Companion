"use client";

import Image from "next/image";
import { useEffect, useState } from "react";
import { getLiveCameraPreview, type LiveCameraFrame } from "@/lib/api";

export function LiveCameraPreview({ jobId, cameraIndex }: { jobId: string; cameraIndex: number }) {
  const [frame, setFrame] = useState<LiveCameraFrame | null>(null);
  const [message, setMessage] = useState(`Opening camera ${cameraIndex}…`);

  useEffect(() => {
    let stopped = false;
    let nextPoll: ReturnType<typeof setTimeout> | undefined;
    let expiry: ReturnType<typeof setTimeout> | undefined;
    let request: AbortController | undefined;
    let resume = false;

    async function poll() {
      if (stopped || document.hidden || request) return;
      const pending = new AbortController();
      request = pending;
      let delay = 250;
      try {
        const preview = await getLiveCameraPreview(jobId, pending.signal);
        if (stopped || document.hidden) return;
        if (preview.job_id !== jobId || preview.camera_index !== cameraIndex) throw new Error("Camera preview does not match this scan.");
        const next = preview.frame;
        const age = next ? Date.now() - next.captured_at * 1_000 : Infinity;
        clearTimeout(expiry);
        if (next && Number.isFinite(age) && age >= -1_000 && age < 2_000
          && next.width > 0 && next.height > 0 && next.width <= 1280 && next.height <= 1280
          && next.data_url.length <= 205_000 && next.data_url.startsWith("data:image/jpeg;base64,")) {
          setFrame(next);
          setMessage(`Live · camera ${cameraIndex}`);
          expiry = setTimeout(() => {
            setFrame(null);
            setMessage("No fresh frames · checking camera connection…");
          }, Math.max(0, 2_000 - Math.max(0, age)));
        } else {
          setFrame(null);
          setMessage(preview.status === "opening_camera" ? `Opening camera ${cameraIndex}…`
            : preview.status === "framing" ? "Waiting for a fresh camera frame…" : "Live preview stopped.");
        }
      } catch (error) {
        if (!stopped && !document.hidden) {
          clearTimeout(expiry);
          setFrame(null);
          setMessage(error instanceof Error ? error.message : "Live camera unavailable.");
        }
        delay = 1_000;
      } finally {
        request = undefined;
        if (!stopped && !document.hidden) {
          nextPoll = setTimeout(poll, resume ? 0 : delay);
          resume = false;
        }
      }
    }

    function onVisibilityChange() {
      clearTimeout(nextPoll);
      clearTimeout(expiry);
      setFrame(null);
      setMessage(document.hidden ? "Live preview paused." : "Reconnecting to the camera…");
      if (document.hidden) {
        resume = false;
        request?.abort();
      } else if (request) resume = true;
      else void poll();
    }

    document.addEventListener("visibilitychange", onVisibilityChange);
    void poll();
    return () => {
      stopped = true;
      clearTimeout(nextPoll);
      clearTimeout(expiry);
      request?.abort();
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [jobId, cameraIndex]);

  return <figure className="live-camera-preview" aria-label="Live book camera">
    <div className="live-camera-feed">
      {frame ? <Image src={frame.data_url} alt={`Live OCR camera ${cameraIndex}`} width={frame.width} height={frame.height} unoptimized />
        : <span>No live image yet</span>}
      {frame && <div className="live-camera-guide" aria-hidden="true" />}
    </div>
    <figcaption role="status">{message}</figcaption>
  </figure>;
}
