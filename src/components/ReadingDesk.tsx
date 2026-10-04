"use client";

import { type FormEvent, useEffect, useRef, useState, useSyncExternalStore } from "react";
import Image from "next/image";
import { completeScanResult, createScanJob, getScanJob, scanJobAction, type ScanJob } from "@/lib/api";
import { useVoiceSession } from "@/lib/voice-session";
import { groupTranscriptFragments } from "@/lib/reading-journal";
import { DeskIcon } from "@/components/DeskIcon";
import { DeveloperPanel, type ScanSnapshot } from "@/components/DeveloperPanel";
import { useEyeDiagnostics } from "@/lib/diagnostics";
import { useScanJobs } from "@/lib/scan-jobs";
import { useTrackerSettings } from "@/lib/tracker-settings";
import notebook from "@/assets/blank note.png";
import bookmark from "@/assets/bookmark_with_no_wrinkle.png";
import loobSymbol from "@/assets/loob-logo-symbol.svg";

type DeskEvent = { id: string; time: number; type: string; message: string };
const initialPassage = [
  "At midnight, Mara stepped into the empty house. Footsteps sounded above her, though no one was supposed to be there. A shadow slid across the stairwell, and a whisper called her name.",
  "She followed the sound to the attic. The door creaked open before she touched it. In the dark, two pale eyes stared from behind a stack of boxes, and Mara held her breath.",
  "A small black cat padded into the moonlight and brushed against her ankle. Mara let out a laugh. The warm glow of a night-light filled the attic, and the house felt safe again.",
].join("\n\n");
const wait = (ms: number) => new Promise<void>((resolve) => window.setTimeout(resolve, ms));
const sessionDateLabel = () => new Intl.DateTimeFormat("en", { weekday: "short", month: "short", day: "numeric" }).format(new Date());
const serverDateLabel = () => "Your reading journal";
function subscribeDate(onChange: () => void) {
  const timer = setInterval(onChange, 60_000);
  return () => clearInterval(timer);
}

export function ReadingDesk() {
  const [tab, setTab] = useState<"reader" | "developer">("reader");
  const [page, setPage] = useState({ id: "sample", text: initialPassage, title: "The Whisper in the Attic" });
  const [lastScan, setLastScan] = useState<ScanSnapshot | null>(null);
  const [pendingCapture, setPendingCapture] = useState<ScanJob | null>(null);
  const [capturedPage, setCapturedPage] = useState<ScanJob | null>(null);
  const [events, setEvents] = useState<DeskEvent[]>([]);
  const [toolStatus, setToolStatus] = useState<{ text: string; error: boolean } | null>(null);
  const [input, setInput] = useState("");
  const [cameraIndex, setCameraIndex] = useState(2);
  const [blinkTestCount, setBlinkTestCount] = useState<number | null>(null);
  const hookOwnedCaptures = useRef(new Set<string>());
  const waitingCapture = useRef<ScanJob | null>(null);
  const journalEnd = useRef<HTMLDivElement | null>(null);
  const sessionDate = useSyncExternalStore(subscribeDate, sessionDateLabel, serverDateLabel);
  const diagnostics = useEyeDiagnostics(true);
  const tracker = useTrackerSettings();

  function logEvent(type: string, message: string) {
    setEvents((current) => [...current, { id: crypto.randomUUID(), time: Date.now() / 1_000, type, message }].slice(-60));
  }
  function report(error: unknown) {
    const text = error instanceof Error ? error.message : "The operation failed. Try again.";
    setToolStatus({ text, error: true });
    logEvent("error", text);
  }
  function applyScan(job: ScanJob) {
    if (!job.result) return;
    setLastScan({ result: job.result, capturedAt: job.result.captured_at * 1_000, durationMs: job.duration_ms ?? 0 });
    setCapturedPage((current) => current?.job_id === job.job_id ? null : current);
    if (job.status === "accepted" && job.result.text) {
      setPage({ id: job.job_id!, text: job.result.text, title: "Scanned book page" });
      setToolStatus({ text: voice.status === "connected" ? "Page scanned. Ask LOOB about it or read it aloud." : "Page scanned. Start conversation to ask about it or read it aloud.", error: false });
    } else if (job.status === "rejected") {
      setToolStatus({ text: `${job.result.reason || "This page was unreadable."} The current page was kept.`, error: true });
    }
  }
  const eyeCameraIndex = tracker.data?.tracker_connected ? tracker.data.eye_camera_index ?? undefined
    : diagnostics.data?.connected ? diagnostics.data.camera_index ?? undefined : undefined;
  const voice = useVoiceSession({
    pageText: page.text,
    pageId: page.id,
    onScanResult: async (jobId, result) => {
      const completed = await completeScanResult(jobId, { accepted: result.accepted, text: result.text, reason: result.reason });
      applyScan(completed);
      hookOwnedCaptures.current.delete(jobId);
    },
    capturePage: async (activeJobId, signal) => {
      signal?.throwIfAborted();
      if (activeJobId) await scanJobAction(activeJobId, "cancel");
      signal?.throwIfAborted();
      let job = await createScanJob(cameraIndex, "test", eyeCameraIndex, signal);
      if (!job.job_id) throw new Error("The camera did not create a capture.");
      const captureId = job.job_id;
      hookOwnedCaptures.current.add(captureId);
      try {
        while (!["captured", "cancelled", "failed"].includes(job.status)) {
          signal?.throwIfAborted();
          await wait(500);
          signal?.throwIfAborted();
          job = await getScanJob(captureId, signal);
        }
        signal?.throwIfAborted();
        if (job.status !== "captured") throw new Error(job.message);
        return job;
      } catch (error) {
        hookOwnedCaptures.current.delete(captureId);
        if (signal?.aborted) await scanJobAction(captureId, "cancel").catch(() => undefined);
        throw error;
      }
    },
  });
  const scans = useScanJobs((job) => {
    if (job.status === "captured") {
      setCapturedPage(job);
      if (job.result) setLastScan({ result: job.result, capturedAt: job.result.captured_at * 1_000, durationMs: job.duration_ms ?? 0 });
      if (!hookOwnedCaptures.current.has(job.job_id!)) {
        if (voice.status === "connected") void voice.submitCapturedPage(job).catch(report);
        else { waitingCapture.current = job; setPendingCapture(job); }
      }
    } else if (job.status === "accepted" || job.status === "rejected") applyScan(job);
    else setToolStatus({ text: job.message, error: job.status === "failed" });
  });
  async function startConversation() {
    await voice.start();
    const capture = waitingCapture.current;
    if (capture) {
      await voice.submitCapturedPage(capture);
      waitingCapture.current = null;
      setPendingCapture(null);
    }
  }
  async function retryProcessing() {
    if (!capturedPage) return;
    await voice.start();
    await voice.submitCapturedPage(capturedPage);
    waitingCapture.current = null;
    setPendingCapture(null);
  }
  useEffect(() => {
    if (tab === "reader") journalEnd.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [voice.fragments, tab]);

  async function scanPage(source: "manual" | "test" = "manual") {
    setToolStatus(null);
    try {
      if (source === "test") {
        for (const count of [1, 2, 3]) { setBlinkTestCount(count); await wait(220); }
      }
      const started = await scans.start(cameraIndex, source, eyeCameraIndex);
      if (!started) setToolStatus({ text: "Scan skipped: the camera is capturing another page.", error: false });
      else logEvent("scan", `Camera ${cameraIndex} capture requested`);
    } catch (error) { report(error); }
    finally { setBlinkTestCount(null); }
  }
  async function controlScan(action: "capture" | "cancel") {
    try { if (action === "capture") await scans.capture(); else await scans.cancel(); }
    catch (error) { report(error); }
  }
  async function send(event: FormEvent) {
    event.preventDefault();
    if (!input.trim()) return;
    const question = input.trim();
    setToolStatus(null);
    try { await voice.sendText(question); setInput(""); }
    catch (error) { report(error); }
  }
  const scanning = scans.active || scans.action !== null;
  const connecting = voice.status === "connecting" || voice.status === "closing";
  const connected = voice.status === "connected";
  const journal = groupTranscriptFragments(voice.fragments);
  const liveEyes = diagnostics.status === "connected" && diagnostics.data?.connected ? diagnostics.data : null;
  const eyeOpenness = liveEyes?.eyes_visible && liveEyes.openness
    ? Math.round((Math.min(1, liveEyes.openness.left) + Math.min(1, liveEyes.openness.right)) * 50) : null;
  const statusText = voice.pendingScanId ? "Reading the captured page…"
    : pendingCapture ? "Page captured. Start conversation to process it."
      : connecting ? "Connecting voice…" : connected ? voice.microphoneMuted ? "Microphone muted" : "Conversation open · listening" : "Conversation ended";
  const connectionButton = <button className="notebook-button conversation-button" type="button" disabled={connecting} onClick={() => connected ? voice.end() : void startConversation().catch(report)}>{connected ? "End conversation" : "Start conversation"}</button>;
  const readControls = <div className="conversation-actions">
    <button className="notebook-button reader-read" type="button" onClick={() => void voice.readPage().catch(report)} disabled={connecting} aria-label="Read story">Read</button>
    <button className="notebook-button" type="button" onClick={voice.stop} disabled={!connected && !connecting} aria-label="Stop audio">Stop</button>
  </div>;
  const scanProcessingControls = capturedPage && <div className="conversation-actions">
    <button className="notebook-button" type="button" disabled={connecting} onClick={() => void retryProcessing().catch(report)}>Retry processing</button>
    <button className="notebook-button" type="button" disabled={!connected} onClick={voice.stop}>Cancel processing</button>
  </div>;

  return <main className="reading-workspace">
    <header className="desk-meta"><div className="app-logo-lockup" aria-label="LOOB"><Image className="app-logo-symbol" src={loobSymbol} alt="" preload /><span className="app-logo-name">LOOB</span></div></header>
    <div className="notebook">
      <div className="notebook-art" aria-hidden="true"><Image src={notebook} alt="" fill sizes="(max-width: 760px) 1600px, 1400px" placeholder="blur" preload /></div>
      <button id="view-bookmark" className="paper-bookmark" type="button" aria-label={tab === "reader" ? "Open Developer view" : "Return to Reader view"} aria-controls={tab === "reader" ? "developer-panel" : "reader-panel"} title={tab === "reader" ? "Open Developer view" : "Return to Reader view"} onClick={() => setTab((current) => current === "reader" ? "developer" : "reader")}>
        <Image src={bookmark} alt="" sizes="208px" draggable={false} /><span className="bookmark-label" aria-hidden="true">{tab === "reader" ? "Developer" : "Reader"} ↗</span>
      </button>
      <div id="reader-panel" className="notebook-spread" role="region" aria-label="Reader" hidden={tab !== "reader"}>
        <section className="notebook-page left-page journal-page" aria-label="Reading journal">
          <header className="page-heading"><span className="folio-heading">01</span><div><h1>{page.title}</h1><p className="page-subtitle">{sessionDate} · reading log</p></div></header>
          <div className="reader-mode"><span>Mode: <strong>{tracker.applied && tracker.data?.applied_blink_only ? scanning ? "Blink test · scan in progress" : "Blink test · ready" : liveEyes?.mode === "SIGNAL" ? "Page signal" : liveEyes?.mode === "STOP" ? "Paused · gaze to restart" : "Reading"}</strong></span>{liveEyes && <span className="mode-note">Eyes open: {eyeOpenness === null ? "—" : `${eyeOpenness}%`}</span>}</div>
          <div className="journal-toolbar"><h2 className="journal-heading">Voice log</h2>{connectionButton}
            <button className="icon-button reader-mic" type="button" disabled={!connected} onClick={voice.toggleMute} aria-label={voice.microphoneMuted ? "Unmute microphone" : "Mute microphone"} aria-pressed={voice.microphoneMuted} title={voice.microphoneMuted ? "Unmute microphone" : "Mute microphone"}><DeskIcon name="mic" width="19" height="19" /></button>
          </div>
          <form className="reader-composer" onSubmit={(event) => void send(event)}><input value={input} onChange={(event) => setInput(event.target.value)} placeholder="Ask about this page…" aria-label="Question about the page" disabled={connecting} maxLength={2000} /><button className="icon-button" type="submit" aria-label="Send question" disabled={connecting || !input.trim()}><DeskIcon name="send" width="22" height="22" /></button></form>
          <p className="reader-status" role="status">{statusText}</p>
          {scanProcessingControls}
          {voice.error && <p className="reader-status is-error" role="alert">{voice.error}</p>}
          {toolStatus && <p className={`reader-status${toolStatus.error ? " is-error" : ""}`} role={toolStatus.error ? "alert" : "status"}>{toolStatus.text}</p>}
          <div className="journal-lines"><div className="journal-messages" role="log" aria-label="Questions and answers" aria-live="polite" tabIndex={0}>{journal.map((entry) => <div key={entry.id} className={`journal-entry ${entry.speaker}`}><span>{entry.text}</span></div>)}<div ref={journalEnd} /></div></div>
          <footer className="page-footer"><span>01 <span className="footer-title">{page.title}</span></span><span>Voice log</span></footer>
        </section>
        <section className="notebook-page right-page story-page" aria-label="Story">
          <header className="story-heading"><span className="page-kicker">{page.id === "sample" ? "01 · Story" : "Scanned page"}</span><div className="story-heading-actions">{readControls}</div></header>
          <article className="story-text" aria-label="Story text" tabIndex={0}>{page.text.split(/\n\s*\n/).filter(Boolean).map((paragraph, index) => <p key={index} className="story-paragraph">{paragraph}</p>)}</article>
          <footer className="page-footer"><span>{page.id === "sample" ? "Sample story" : "Current book page"}</span><span>02</span></footer>
        </section>
      </div>
      <div id="developer-panel" className="notebook-spread" role="region" aria-label="Developer" hidden={tab !== "developer"}>
        <DeveloperPanel visible={tab === "developer"} diagnostics={diagnostics} tracker={tracker} lastScan={lastScan} scanJob={scans.job} scanConnected={scans.connected} scanError={scans.error} scanAction={scans.action} scanDisabled={scanning || !scans.connected || blinkTestCount !== null} blinkTestCount={blinkTestCount} cameraIndex={cameraIndex} onCameraIndexChange={setCameraIndex} onScan={() => void scanPage()} onCapture={() => void controlScan("capture")} onCancel={() => void controlScan("cancel")} onTestThreeBlinks={() => void scanPage("test")} events={events}>
          <section className="reader-tools" aria-labelledby="reader-tools-heading"><h3 id="reader-tools-heading">Reader controls</h3>{connectionButton}{readControls}<p className="tool-status" role="status">{statusText}</p>{scanProcessingControls}{voice.error && <p className="tool-status is-error" role="alert">{voice.error}</p>}{toolStatus && <p className={`tool-status${toolStatus.error ? " is-error" : ""}`} role={toolStatus.error ? "alert" : "status"}>{toolStatus.text}</p>}</section>
        </DeveloperPanel>
      </div>
    </div>
  </main>;
}
