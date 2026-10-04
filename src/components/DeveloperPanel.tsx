"use client";

import type { ReactNode } from "react";
import type { CameraScan } from "@/lib/api";
import type { DiagnosticEvent, EyeDiagnosticsState } from "@/lib/diagnostics";
import { ScanPreview } from "@/components/ScanPreview";

export type ScanSnapshot = { result: CameraScan; capturedAt: number; durationMs: number };

type DeveloperPanelProps = {
  diagnostics: EyeDiagnosticsState;
  lastScan: ScanSnapshot | null;
  scanning: boolean;
  scanDisabled?: boolean;
  cameraIndex: number;
  onCameraIndexChange: (index: number) => void;
  onScan: () => void;
  pageText: string;
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

export function DeveloperPanel({ diagnostics, lastScan, scanning, scanDisabled = false, cameraIndex, onCameraIndexChange, onScan, events, children }: DeveloperPanelProps) {
  const data = diagnostics.data;
  const connected = diagnostics.status === "connected" && data?.connected === true;
  const gaze = connected && data.eyes_visible ? data.gaze : null;
  const openness = connected && data.eyes_visible ? data.openness : null;
  const meanOpenness = openness ? (clamp(openness.left) + clamp(openness.right)) / 2 : null;
  const sortedEvents = [...events, ...(data?.events ?? [])]
    .filter((event, index, all) => all.findIndex((item) => item.id === event.id) === index)
    .sort((a, b) => b.time - a.time)
    .slice(0, 20);
  const confidence = lastScan?.result.metrics.vision_confidence;
  const scanWordCount = lastScan?.result.text.trim() ? lastScan.result.text.trim().split(/\s+/).length : 0;

  return <>
    <section className="notebook-page developer-page left-page" aria-labelledby="eye-camera-heading">
      <header className="appendix-heading">
        <span className="appendix-number" aria-hidden="true">A1</span>
        <div><h1 id="eye-camera-heading">Eye camera</h1><p>MediaPipe · {connected ? "live" : "offline"}</p></div>
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
          <p className="monitor-caption">{connected ? data.eyes_visible ? "live" : "eyes not visible" : "tracker offline"}</p>
        </div>
        <dl className="diagnostic-metrics eye-metrics">
          <div><dt>Eyes open</dt><dd>{meanOpenness === null ? "—" : `${Math.round(meanOpenness * 100)}%`}</dd></div>
          <div><dt>Blinks</dt><dd>{connected ? data.blink_count : "—"}</dd></div>
          <div><dt>Gaze</dt><dd>{directionLabel(gaze)}</dd></div>
          <div><dt>Stare timer</dt><dd>{connected ? `${data.look_progress.toFixed(1)} / 3s` : "—"}</dd></div>
        </dl>
      </div>
      <footer className="page-footer"><span>A1 · Appendix</span><span className={`connection-label${connected ? " is-connected" : ""}`}>{connected ? "tracker live" : "tracker offline"}</span></footer>
    </section>

    <section className="notebook-page developer-page right-page" aria-labelledby="book-camera-heading">
      <header className="appendix-heading">
        <span className="appendix-number" aria-hidden="true">A2</span>
        <div><h2 id="book-camera-heading">Book camera</h2><p>OCR · latest capture</p></div>
      </header>
      <div className="developer-body">
        <div className="capture-toolbar">
          <span className={`capture-status${lastScan?.result.accepted ? " status-reading" : ""}`} role="status">{scanning ? "scanning…" : lastScan ? lastScan.result.accepted ? "accepted" : "not accepted" : "—"}</span>
          <div className="developer-scan-controls">
            <label htmlFor="developer-camera-index">Cam <input id="developer-camera-index" aria-label="Book camera index" type="number" min="0" max="10" step="1" value={cameraIndex} disabled={scanning || scanDisabled} onChange={(event) => { const next = Number(event.target.value); if (Number.isInteger(next) && next >= 0 && next <= 10) onCameraIndexChange(next); }} /></label>
            <button type="button" className="notebook-button" onClick={onScan} disabled={scanning || scanDisabled}>{scanning ? "Scanning…" : "Scan"}</button>
          </div>
        </div>
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
      <footer className="page-footer"><span className={`connection-label${diagnostics.status === "connected" ? " is-connected" : ""}`}>API {diagnostics.status === "connected" ? "live" : "offline"}</span><span>Appendix · A2</span></footer>
    </section>
  </>;
}
