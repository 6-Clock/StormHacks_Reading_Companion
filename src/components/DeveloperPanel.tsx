"use client";

import { useEffect, useState, type ReactNode } from "react";
import { scanIsActive, type CameraScan, type ScanJob, type ScanStatus } from "@/lib/api";
import type { TrackerSettingsControl } from "@/lib/tracker-settings";
import type { DiagnosticEvent, EyeDiagnosticsState } from "@/lib/diagnostics";
import { ScanPreview } from "@/components/ScanPreview";
import { LiveCameraPreview } from "@/components/LiveCameraPreview";

export type ScanSnapshot = { result: CameraScan; capturedAt: number; durationMs: number };

type DeveloperPanelProps = {
  visible: boolean;
  diagnostics: EyeDiagnosticsState;
  tracker: TrackerSettingsControl;
  lastScan: ScanSnapshot | null;
  scanJob: ScanJob | null;
  scanConnected: boolean;
  scanError: string | null;
  scanAction: "starting" | "capture" | "cancel" | null;
  scanDisabled?: boolean;
  blinkTestCount: number | null;
  cameraIndex: number;
  onCameraIndexChange: (index: number) => void;
  onScan: () => void;
  onCapture: () => void;
  onCancel: () => void;
  onTestThreeBlinks: () => void;
  events: DiagnosticEvent[];
  children?: ReactNode;
};

const clamp = (value: number) => Math.max(0, Math.min(1, value));

function numberLabel(value: number | null | undefined, suffix = "") {
  return typeof value === "number" && Number.isFinite(value) ? `${Math.round(value)}${suffix}` : "—";
}

function clockLabel(seconds: number) {
  return new Date(seconds * 1_000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function directionLabel(gaze: { x: number; y: number } | null) {
  if (!gaze) return "—";
  const horizontal = gaze.x < 0.4 ? "left" : gaze.x > 0.6 ? "right" : "";
  const vertical = gaze.y < 0.4 ? "top" : gaze.y > 0.6 ? "bottom" : "";
  return [vertical, horizontal].filter(Boolean).join(" ") || "center";
}

const scanLabels: Record<ScanStatus, string> = {
  idle: "Ready to scan", reserved: "Page turn reserved", queued: "Starting scan",
  settling: "Waiting for page to settle", opening_camera: "Opening camera",
  waiting_for_eye_camera: "Waiting for eye camera to close",
  framing: "Frame the page", capturing: "Capturing page", transcribing: "Reading text",
  reviewing: "Checking text", preview: "Preparing preview", cancelling: "Cancelling scan…",
  accepted: "Page accepted", rejected: "Page not clear enough", unchanged: "Same page",
  cancelled: "Scan cancelled", failed: "Scan failed", timed_out: "Scan timed out",
};

function PageTurnCountdown({ startsAt, duration }: { startsAt: number; duration: number }) {
  const [now, setNow] = useState<number | null>(null);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 100);
    return () => clearInterval(timer);
  }, []);
  const remaining = Math.max(0, Math.ceil(now === null ? duration : startsAt - now / 1_000));
  return <p className="auto-scan-status" role="timer" aria-label="Page-turn wait">
    {remaining > 0 ? `OCR starts in ${remaining}s · waiting for the page to turn.` : "Page-turn wait complete · preparing OCR…"}
  </p>;
}

export function DeveloperPanel({ visible, diagnostics, tracker, lastScan, scanJob, scanConnected, scanError, scanAction, scanDisabled = false, blinkTestCount, cameraIndex, onCameraIndexChange, onScan, onCapture, onCancel, onTestThreeBlinks, events, children }: DeveloperPanelProps) {
  const data = diagnostics.data;
  const cameraState = tracker.connected && tracker.data?.tracker_connected ? tracker.data.eye_camera_state : null;
  const pauseRequested = tracker.connected && !!tracker.data?.camera_pause_job_id;
  const cameraPaused = pauseRequested && cameraState === "released";
  const eyeStatus = cameraPaused ? "paused for OCR" : cameraState === "closing" ? "closing for OCR"
    : cameraState === "opening" ? "opening" : cameraState === "error" ? "camera error" : null;
  const connected = !eyeStatus && diagnostics.status === "connected" && data?.connected === true;
  const gaze = connected && data.eyes_visible ? data.gaze : null;
  const openness = connected && data.eyes_visible ? data.openness : null;
  const meanOpenness = openness ? (clamp(openness.left) + clamp(openness.right)) / 2 : null;
  const sortedEvents = [...events, ...(data?.events ?? []), ...(scanJob?.events ?? [])]
    .filter((event, index, all) => all.findIndex((item) => item.id === event.id) === index)
    .sort((a, b) => b.time - a.time)
    .slice(0, 20);
  const confidence = lastScan?.result.metrics.vision_confidence;
  const scanWordCount = lastScan?.result.text.trim() ? lastScan.result.text.trim().split(/\s+/).length : 0;
  const scanning = scanIsActive(scanJob);
  const blinkOnly = tracker.applied && tracker.data?.applied_blink_only === true;
  const trackerStatus = !tracker.connected ? "Settings service offline"
    : !tracker.data?.tracker_connected ? "Tracker offline · setting waits for connection"
      : eyeStatus ? `Eye camera ${eyeStatus} · blinks paused`
        : tracker.saving || !tracker.applied ? "Applying to eye tracker…"
        : blinkOnly ? scanning ? "Blinks paused while scanning" : "Blink-only active · stare disabled"
          : "3-second gaze required to enter Read mode";
  const captureStatus = !scanConnected ? "Scan service offline" : scanAction === "starting" ? "Starting scan…" : scanLabels[scanJob?.status ?? "idle"];
  const recoveryDisabled = !scanConnected || scanAction !== null || scanJob?.status === "cancelling";
  const blinkReadiness = cameraPaused ? "Eye camera released for OCR. Tracking resumes when this scan finishes."
    : cameraState === "closing" ? "Closing the eye camera before OCR opens."
      : cameraState === "opening" ? "Opening the eye camera. A fresh blink sequence will be required."
        : cameraState === "error" ? "Eye camera unavailable. Check the tracker window and camera connection."
          : !connected ? "Waiting for live eye measurements."
    : data.calibrated === false ? "Press C in the eye camera window and keep your eyes open to calibrate."
      : !data.eyes_visible ? "Move into the eye camera view so both eyes are visible."
        : data.calibrated !== true ? "Restart the eye tracker to enable live blink readiness."
          : data.turns_blocked || scanning || data.mode === "SIGNAL" ? "Page turns paused while the scanner or tracker controls are busy."
            : !data.blink_only && data.mode !== "READ" ? "Hold your gaze for 3 seconds, or enable Blink-only test mode."
              : "Ready — blink three times within two seconds.";
  const lastCameraGesture = data?.events.filter((event) => event.type === "blink" && /^Blink 3\/3 recorded/.test(event.message))
    .sort((a, b) => b.time - a.time)[0];

  return <>
    <section className="notebook-page developer-page left-page" aria-labelledby="eye-camera-heading">
      <header className="appendix-heading">
        <span className="appendix-number" aria-hidden="true">A1</span>
        <div><h1 id="eye-camera-heading">Eye camera</h1><p>MediaPipe · {eyeStatus ?? (connected ? "live" : "offline")}</p></div>
      </header>
      <div className="developer-body">
        <div className={`eye-monitor${connected ? " is-connected" : ""}`}>
          <p className="gaze-label">{connected ? `mode: ${data.mode?.toLowerCase() ?? "—"}` : "—"}</p>
          <svg className="eye-diagram" viewBox="0 0 480 140" role="img" aria-label={gaze ? `Estimated gaze ${directionLabel(gaze)}` : "Eye tracking diagram, no gaze estimate available"}>
            <defs>
              <clipPath id="left-eye-clip"><ellipse cx="135" cy="70" rx="75" ry={openness ? 8 + clamp(openness.left) * 27 : 28} /></clipPath>
              <clipPath id="right-eye-clip"><ellipse cx="345" cy="70" rx="75" ry={openness ? 8 + clamp(openness.right) * 27 : 28} /></clipPath>
            </defs>
            {[{ cx: 135, side: "left" as const }, { cx: 345, side: "right" as const }].map(({ cx, side }) => <g key={side}>
              <ellipse className="eye-outline" cx={cx} cy="70" rx="75" ry={openness ? 8 + clamp(openness[side]) * 27 : 28} fill="none" stroke="currentColor" strokeWidth="2" />
              {gaze && <g clipPath={`url(#${side}-eye-clip)`}>
                <circle className="eye-iris" cx={cx + (clamp(gaze.x) - 0.5) * 85} cy={70 + (clamp(gaze.y) - 0.5) * 34} r="21" fill="none" stroke="currentColor" strokeWidth="2.5" />
                <circle className="eye-pupil" cx={cx + (clamp(gaze.x) - 0.5) * 85} cy={70 + (clamp(gaze.y) - 0.5) * 34} r="5" fill="currentColor" />
              </g>}
            </g>)}
          </svg>
          <p className="monitor-caption">{eyeStatus ?? (connected ? data.eyes_visible ? "live" : "eyes not visible" : "tracker offline")}</p>
        </div>
        <dl className="diagnostic-metrics eye-metrics">
          <div><dt>Eyes open</dt><dd>{meanOpenness === null ? "—" : `${Math.round(meanOpenness * 100)}%`}</dd></div>
          <div><dt>Blinks</dt><dd>{connected ? `${data.blink_count} / 3` : "—"}</dd></div>
          <div><dt>Gaze</dt><dd>{directionLabel(gaze)}</dd></div>
          <div><dt>Stare timer</dt><dd>{blinkOnly ? "Off · test mode" : connected ? `${data.look_progress.toFixed(1)} / 3s` : "—"}</dd></div>
        </dl>
        <div className="camera-blink-status" role="status" aria-label="Camera blink detection">
          <p>{blinkReadiness}</p>
          {lastCameraGesture && <p className="status-reading">Camera recorded 3 / 3 at {clockLabel(lastCameraGesture.time)}.</p>}
        </div>
        <section className="developer-test-controls" aria-labelledby="developer-test-heading">
          <h3 id="developer-test-heading">Developer test</h3>
          <label className="tracker-mode-control">
            <input type="checkbox" role="switch" checked={tracker.data?.blink_only ?? false} disabled={tracker.saving || !tracker.connected} onChange={(event) => void tracker.toggle(event.target.checked)} aria-describedby="tracker-setting-status" />
            <span>Blink-only test mode</span>
          </label>
          <p id="tracker-setting-status" role="status">{trackerStatus}</p>
          <p>When enabled, three blinks within two seconds can turn the page without entering Read mode. Page turns pause during scanning.</p>
          {tracker.error && <p className="control-error" role="alert">{tracker.error}</p>}
          <button type="button" className="notebook-button" onClick={onTestThreeBlinks} disabled={scanDisabled || blinkTestCount !== null}>
            {blinkTestCount === null ? "Test 3 blinks" : `Blink ${blinkTestCount} / 3`}
          </button>
          <p>Simulates three blinks and starts camera {cameraIndex} OCR. This button does not move the page-turn hardware.</p>
        </section>
      </div>
      <footer className="page-footer"><span>A1 · Appendix</span><span className={`connection-label${connected ? " is-connected" : ""}`}>{eyeStatus ?? (connected ? "tracker live" : "tracker offline")}</span></footer>
    </section>

    <section className="notebook-page developer-page right-page" aria-labelledby="book-camera-heading">
      <header className="appendix-heading">
        <span className="appendix-number" aria-hidden="true">A2</span>
        <div><h2 id="book-camera-heading">Book camera</h2><p>Live preview · OCR</p></div>
      </header>
      <div className="developer-body">
        <div className="capture-toolbar">
          <span className={`capture-status${scanJob?.status === "accepted" ? " status-reading" : ""}`} role="status">{captureStatus}</span>
          <div className="developer-scan-controls">
            <label htmlFor="developer-camera-index">Cam <input id="developer-camera-index" aria-label="Book camera index" type="number" min="0" max="10" step="1" value={cameraIndex} disabled={scanning || scanDisabled} onChange={(event) => { const next = Number(event.target.value); if (Number.isInteger(next) && next >= 0 && next <= 10) onCameraIndexChange(next); }} /></label>
            <button type="button" className="notebook-button" onClick={onScan} disabled={scanning || scanDisabled}>Live camera</button>
            <button type="button" className="notebook-button" onClick={onScan} disabled={scanning || scanDisabled}>{scanning ? "Scanning…" : "Scan OCR"}</button>
          </div>
        </div>
        {scanJob?.status === "settling" && typeof scanJob.scan_starts_at === "number"
          ? <PageTurnCountdown key={scanJob.job_id} startsAt={scanJob.scan_starts_at} duration={scanJob.settle_seconds ?? 8} />
          : scanJob?.job_id && <p className="auto-scan-status" role="status">{scanJob.message}</p>}
        {scanning && <p className="auto-scan-status">New scan requests are skipped until this scan finishes.</p>}
        {!scanning && <p className="auto-scan-status">Live camera and Scan OCR work with the eye tracker closed. If it is running, LOOB pauses it and resumes it after scanning.</p>}
        {scanError && <p className="control-error" role="alert">{scanError}</p>}
        {scanning && <div className="scan-recovery-controls">
          {scanJob?.status === "framing" && <button type="button" className="notebook-button" onClick={onCapture} disabled={recoveryDisabled}>{scanAction === "capture" ? "Capturing…" : "Capture now"}</button>}
          <button type="button" className="notebook-button" onClick={onCancel} disabled={recoveryDisabled}>{scanAction === "cancel" || scanJob?.status === "cancelling" ? "Cancelling…" : "Cancel scan"}</button>
        </div>}
        {visible && scanJob?.job_id && scanJob.source === "manual" && ["opening_camera", "framing"].includes(scanJob.status)
          && <LiveCameraPreview key={scanJob.job_id} jobId={scanJob.job_id} cameraIndex={scanJob.camera_index ?? cameraIndex} />}
        {scanJob?.status === "framing" && <p className="auto-scan-status">Position the page inside the guide, then choose Capture now to run OCR. Cancel scan closes the live camera. Preview reserves the camera and expires after 60 seconds.</p>}
        <ScanPreview preview={lastScan?.result.capture_preview ?? null} />
        {lastScan && <details className="ocr-details"><summary>OCR details</summary>
          <dl className="diagnostic-metrics">
            <div><dt>Words</dt><dd>{scanWordCount}</dd></div>
            <div><dt>Confidence</dt><dd>{typeof confidence === "number" ? numberLabel(confidence * 100, "%") : "—"}</dd></div>
            <div><dt>Sharpness</dt><dd>{typeof lastScan.result.metrics.sharpness === "number" ? numberLabel(lastScan.result.metrics.sharpness) : "—"}</dd></div>
            <div><dt>Brightness</dt><dd>{typeof lastScan.result.metrics.brightness === "number" ? numberLabel(lastScan.result.metrics.brightness) : "—"}</dd></div>
            <div><dt>Model</dt><dd>{lastScan.result.openai_review?.model ?? "—"}</dd></div>
            <div><dt>Revision</dt><dd>{lastScan.result.openai_revision_status.replaceAll("_", " ")}</dd></div>
            <div><dt>Scan time</dt><dd>{(lastScan.durationMs / 1_000).toFixed(1)}s</dd></div>
            <div><dt>Captured</dt><dd>{clockLabel(lastScan.capturedAt / 1_000)}</dd></div>
          </dl>
          <p className="scan-reason">{lastScan.result.reason.replaceAll("_", " ")}</p>
        </details>}
        {children && <div className="developer-reader-controls">{children}</div>}
        <section className="command-log" aria-labelledby="command-log-heading">
          <h3 id="command-log-heading">Command log</h3>
          {sortedEvents.length ? <ol className="command-events">{sortedEvents.map((event) => <li key={event.id} className={`command-event event-${event.type.replace(/[^a-zA-Z0-9_-]/g, "")}`}><time dateTime={new Date(event.time * 1_000).toISOString()}>{clockLabel(event.time)}</time><span>{event.message}</span></li>)}</ol> : <p className="empty-log">— no events yet —</p>}
        </section>
      </div>
      <footer className="page-footer"><span className={`connection-label${scanConnected ? " is-connected" : ""}`}>API {scanConnected ? "live" : "offline"}</span><span>Appendix · A2</span></footer>
    </section>
  </>;
}
