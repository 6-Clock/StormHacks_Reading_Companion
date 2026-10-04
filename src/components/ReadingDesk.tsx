"use client";

import { FormEvent, useEffect, useRef, useState, useSyncExternalStore } from "react";
import Image from "next/image";
import { ask, LoobError, makeSpeech, planNarration, scanCamera, transcribe, type CameraScan, type NarrationMood, type NarrationPlan, type StoryEffect } from "@/lib/api";
import { AmbientSound } from "@/lib/ambient";
import { DeskIcon } from "@/components/DeskIcon";
import { DeveloperPanel } from "@/components/DeveloperPanel";
import { useEyeDiagnostics } from "@/lib/diagnostics";
import { countAskedWords, findAskedTerm } from "@/lib/reading-journal";
import notebook from "@/assets/blank note.png";
import bookmark from "@/assets/bookmark_with_no_wrinkle.png";
import stickyNote from "@/assets/postit yellow png.png";
import loobSymbol from "@/assets/loob-logo-symbol.svg";

type Message = { id: number; role: "user" | "assistant" | "notice" | "error"; text: string; voice?: boolean };
type DeskEvent = { id: string; time: number; type: string; message: string };

const initialPassage = [
  "At midnight, Mara stepped into the empty house. Footsteps sounded above her, though no one was supposed to be there. A shadow slid across the stairwell, and a whisper called her name.",
  "She followed the sound to the attic. The door creaked open before she touched it. In the dark, two pale eyes stared from behind a stack of boxes, and Mara held her breath.",
  "A small black cat padded into the moonlight and brushed against her ankle. Mara let out a laugh. The warm glow of a night-light filled the attic, and the house felt safe again.",
];

const timestamp = () => Date.now();
const sessionDateLabel = () => new Intl.DateTimeFormat("en", { weekday: "short", month: "short", day: "numeric" }).format(new Date());
const serverDateLabel = () => "Your reading journal";
function subscribeDate(onChange: () => void) {
  const timer = setInterval(onChange, 60_000);
  return () => clearInterval(timer);
}

function scanError(reason: string) {
  if (reason.includes("scan_cancelled")) return "Camera calibration was cancelled. The current page was kept.";
  if (reason.includes("no_camera_frames")) return "The camera did not return a frame. Check that it is connected and try again.";
  if (reason.includes("openai_request_failed")) return "AI OCR could not reach the configured vision model. Check the Uvicorn terminal for the exact API error and verify OPENAI_OCR_MODEL.";
  if (reason.includes("invalid_openai_response")) return "The vision model returned an unreadable response. Check the Uvicorn terminal and try a vision-capable OPENAI_OCR_MODEL.";
  if (reason.includes("openai_revision_rejected")) return "AI OCR found text but could not verify it reliably. Improve focus or lighting and scan again.";
  if (reason.includes("openai_text_not_plausible")) return "AI OCR could not find enough readable printed text on this page. Center the text and scan again.";
  if (reason.includes("too_few_confident_words") || reason.includes("non_text")) return "No readable book text was detected. Center the page, improve lighting, and scan again.";
  if (reason.includes("low_text_confidence")) return "The page is too blurry or dim to read reliably. Refocus the camera and scan again.";
  return "This scan was not clear enough to use. Reposition the book and try again.";
}

function speechChunks(text: string) {
  const chunks: string[] = [];
  let remaining = text.trim();
  while (remaining.length > 4_500) {
    const space = remaining.lastIndexOf(" ", 4_500);
    const end = space > 3_000 ? space : 4_500;
    chunks.push(remaining.slice(0, end).trim());
    remaining = remaining.slice(end).trim();
  }
  if (remaining) chunks.push(remaining);
  return chunks;
}

function storyParagraphs(text: string) {
  const blocks = text.trim().split(/\n\s*\n/).map((block) => block.replace(/\s*\n\s*/g, " ").trim()).filter(Boolean);
  if (blocks.length !== 1 || blocks[0].length <= 450) return blocks;

  // OCR sometimes returns one uninterrupted block. Group sentences so mood can still change.
  const sentences = blocks[0].match(/[^.!?]+(?:[.!?]+[”"']?)|[^.!?]+$/g)?.map((sentence) => sentence.trim()).filter(Boolean) ?? [];
  if (sentences.length < 2) return blocks;
  const grouped: string[] = [];
  let current = "";
  for (const sentence of sentences) {
    if (current && current.length + sentence.length > 320) {
      grouped.push(current);
      current = sentence;
    } else {
      current = current ? `${current} ${sentence}` : sentence;
    }
  }
  if (current) grouped.push(current);
  return grouped;
}

export function ReadingDesk() {
  const [tab, setTab] = useState<"reader" | "developer">("reader");
  const [lastScan, setLastScan] = useState<{ result: CameraScan; capturedAt: number; durationMs: number } | null>(null);
  const [events, setEvents] = useState<DeskEvent[]>([]);
  const [askedTerms, setAskedTerms] = useState<string[]>([]);
  const [wordNote, setWordNote] = useState<{ term: string; answer: string } | null>(null);
  const sessionDate = useSyncExternalStore(subscribeDate, sessionDateLabel, serverDateLabel);
  const diagnostics = useEyeDiagnostics(true);
  const [messages, setMessages] = useState<Message[]>([]);
  const [toolStatus, setToolStatus] = useState<{ text: string; error: boolean } | null>(null);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [recording, setRecording] = useState(false);
  const [micPending, setMicPending] = useState(false);
  const [speaking, setSpeaking] = useState<number | null>(null);
  const [pageText, setPageText] = useState(() => initialPassage.join("\n\n"));
  const [pageTitle, setPageTitle] = useState("The Whisper in the Attic");
  const [pageScanned, setPageScanned] = useState(false);
  const [cameraIndex, setCameraIndex] = useState(1);
  const [scanning, setScanning] = useState(false);
  const [immersive, setImmersive] = useState(false);
  const [ambientVolume, setAmbientVolume] = useState(60);
  const [activeParagraph, setActiveParagraph] = useState<number | null>(null);
  const [pageMoods, setPageMoods] = useState<NarrationMood[] | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const micWorking = useRef(false);
  const messageSequence = useRef(0);
  const stream = useRef<MediaStream | null>(null);
  const audio = useRef<HTMLAudioElement | null>(null);
  const audioUrl = useRef<string | null>(null);
  const speechRequest = useRef<AbortController | null>(null);
  const playbackVersion = useRef(0);
  const ambience = useRef<AmbientSound | null>(null);
  const planCache = useRef<{ text: string; plan: NarrationPlan } | null>(null);
  const speechCache = useRef(new Map<string, Blob>());
  const journalEnd = useRef<HTMLDivElement | null>(null);
  const questionInput = useRef<HTMLInputElement | null>(null);

  function logEvent(type: string, message: string) {
    setEvents((current) => [...current, { id: crypto.randomUUID(), time: Date.now() / 1000, type, message }].slice(-60));
  }

  function add(message: Omit<Message, "id">) {
    if (message.role === "notice" || message.role === "error") setToolStatus({ text: message.text, error: message.role === "error" });
    const id = ++messageSequence.current;
    setMessages((items) => [...items, { ...message, id }]);
  }

  function stopAudio() {
    playbackVersion.current += 1;
    speechRequest.current?.abort();
    speechRequest.current = null;
    audio.current?.pause();
    if (audioUrl.current) URL.revokeObjectURL(audioUrl.current);
    audio.current = null;
    audioUrl.current = null;
    ambience.current?.stop();
    ambience.current = null;
    setSpeaking(null);
    setActiveParagraph(null);
  }

  async function playClip(blobRequest: Promise<Blob>, mood: NarrationMood, controller: AbortController, version: number, useAmbience: boolean, effect?: StoryEffect, onReady?: () => void) {
    const blob = await blobRequest;
    if (controller.signal.aborted || playbackVersion.current !== version) return false;

    const url = URL.createObjectURL(blob);
    const player = new Audio(url);
    audio.current = player;
    audioUrl.current = url;
    if (useAmbience) {
      ambience.current ??= new AmbientSound();
      const sound = ambience.current;
      sound.setVolume(ambientVolume / 100);
      try {
        await sound.setMood(mood);
      } catch {
        sound.stop();
        if (ambience.current === sound) ambience.current = null;
      }
    }
    if (controller.signal.aborted || playbackVersion.current !== version) {
      URL.revokeObjectURL(url);
      return false;
    }
    onReady?.();

    return new Promise<boolean>((resolve, reject) => {
      let finished = false;
      const finish = (completed: boolean, error?: Error) => {
        if (finished) return;
        finished = true;
        controller.signal.removeEventListener("abort", onAbort);
        if (audio.current === player) audio.current = null;
        if (audioUrl.current === url) audioUrl.current = null;
        URL.revokeObjectURL(url);
        if (error) reject(error);
        else resolve(completed);
      };
      const onAbort = () => { player.pause(); finish(false); };
      controller.signal.addEventListener("abort", onAbort, { once: true });
      player.onended = () => finish(true);
      player.onerror = () => finish(false, new LoobError("Voice playback failed. Tap to try again."));
      void player.play().then(() => {
        if (!finished && useAmbience && effect) ambience.current?.playEffect(effect);
      }).catch((error: Error) => finish(false, error));
    });
  }

  async function play(text: string, id: number) {
    if (scanning || recording || micWorking.current) return;
    setToolStatus(null);
    stopAudio();
    const version = playbackVersion.current;
    const controller = new AbortController();
    speechRequest.current = controller;
    setSpeaking(id);
    try {
      await playClip(makeSpeech(text, controller.signal), "neutral", controller, version, false);
      if (playbackVersion.current === version) stopAudio();
    } catch (error) {
      if (controller.signal.aborted) return;
      if (playbackVersion.current === version) stopAudio();
      add({ role: "notice", text: error instanceof LoobError ? error.message : "LOOB could not prepare that voice response." });
    }
  }

  async function narratePage(startIndex: number, fullPage: boolean) {
    if (scanning || recording || busy || micWorking.current) return;
    setToolStatus(null);
    const paragraphs = storyParagraphs(pageText);
    if (!paragraphs.length) return;
    logEvent("narration", `${fullPage ? "Page" : `Paragraph ${startIndex + 1}`} narration · ${immersive ? "immersive" : "regular"}`);
    stopAudio();
    const version = playbackVersion.current;
    const controller = new AbortController();
    speechRequest.current = controller;
    setSpeaking(-(startIndex + 1));
    setActiveParagraph(startIndex);
    if (immersive) {
      ambience.current = new AmbientSound();
      ambience.current.setVolume(ambientVolume / 100);
      void ambience.current.unlock().catch(() => undefined);
    }

    try {
      let moods: NarrationMood[] = paragraphs.map(() => "neutral");
      let plan: NarrationPlan | null = null;
      if (immersive) {
        if (planCache.current?.text === pageText) {
          plan = planCache.current.plan;
          moods = plan.moods;
        } else {
          try {
            plan = await planNarration(paragraphs, controller.signal);
            if (controller.signal.aborted || playbackVersion.current !== version) return;
            moods = plan.moods;
            planCache.current = { text: pageText, plan };
            setPageMoods(moods);
          } catch {
            if (controller.signal.aborted || playbackVersion.current !== version) return;
            add({ role: "notice", text: "Scene sounds are unavailable, so this page will use the regular voice." });
          }
        }
      }

      const end = fullPage ? paragraphs.length : startIndex + 1;
      const segments: Array<{ text: string; paragraphIndex: number; mood: NarrationMood; effect?: StoryEffect }> = [];
      for (let paragraphIndex = startIndex; paragraphIndex < end; paragraphIndex++) {
        const mood = moods[paragraphIndex] ?? "neutral";
        const cues = plan?.cues.filter((cue) => cue.paragraph_index === paragraphIndex) ?? [];
        const sentences = plan?.sentences[paragraphIndex];
        if (!cues.length || !sentences?.length) {
          speechChunks(paragraphs[paragraphIndex]).forEach((text) => segments.push({ text, paragraphIndex, mood }));
          continue;
        }

        const cueBySentence = new Map(cues.map((cue) => [cue.sentence_index, cue.effect]));
        let pending = "";
        const flushPending = () => {
          speechChunks(pending).forEach((text) => segments.push({ text, paragraphIndex, mood }));
          pending = "";
        };
        sentences.forEach((sentence, sentenceIndex) => {
          const effect = cueBySentence.get(sentenceIndex);
          if (effect) {
            flushPending();
            speechChunks(sentence).forEach((text, chunkIndex) =>
              segments.push({ text, paragraphIndex, mood, effect: chunkIndex === 0 ? effect : undefined }),
            );
          } else {
            pending = pending ? `${pending} ${sentence}` : sentence;
          }
        });
        flushPending();
      }
      const requests = new Map<number, Promise<Blob>>();
      const requestSegment = (segmentIndex: number) => {
        if (!requests.has(segmentIndex)) {
          const segment = segments[segmentIndex];
          const key = `${segment.mood}\u0000${segment.text}`;
          const cached = speechCache.current.get(key);
          const request = cached ? Promise.resolve(cached) : makeSpeech(segment.text, controller.signal, segment.mood).then((blob) => {
            if (!controller.signal.aborted) {
              if (speechCache.current.size >= 50) speechCache.current.delete(speechCache.current.keys().next().value!);
              speechCache.current.set(key, blob);
            }
            return blob;
          });
          void request.catch(() => undefined);
          requests.set(segmentIndex, request);
        }
        return requests.get(segmentIndex)!;
      };
      for (let index = 0; index < segments.length; index++) {
        if (controller.signal.aborted || playbackVersion.current !== version) return;
        const segment = segments[index];
        setSpeaking(-(segment.paragraphIndex + 1));
        setActiveParagraph(segment.paragraphIndex);
        const completed = await playClip(requestSegment(index), segment.mood, controller, version, immersive, segment.effect, () => {
          if (index + 1 < segments.length) requestSegment(index + 1);
        });
        if (!completed) return;
      }
      if (playbackVersion.current === version) stopAudio();
    } catch (error) {
      if (controller.signal.aborted || playbackVersion.current !== version) return;
      stopAudio();
      add({ role: "notice", text: error instanceof LoobError ? error.message : "LOOB could not narrate this page." });
    }
  }

  async function submit(question: string, fromMic = false) {
    const cleaned = question.trim();
    if (!cleaned || busy || scanning || (!fromMic && micWorking.current)) return;
    setToolStatus(null);
    logEvent("question", fromMic ? "Voice question submitted" : "Reader question submitted");
    stopAudio();
    add({ role: "user", text: cleaned, voice: fromMic });
    const askedTerm = findAskedTerm(cleaned, pageText);
    if (askedTerm) setAskedTerms((terms) => [...terms, askedTerm]);
    setInput("");
    setBusy(true);
    try {
      const result = await ask(cleaned, pageText);
      const id = ++messageSequence.current;
      setMessages((items) => [...items, { id, role: "assistant", text: result.answer }]);
      if (askedTerm) setWordNote({ term: askedTerm, answer: result.answer });
      void play(result.answer, id);
    } catch (error) {
      add({ role: "error", text: error instanceof LoobError ? error.message : "LOOB could not answer that question." });
    } finally {
      setBusy(false);
    }
  }

  async function scanPage() {
    if (scanning || busy || recording || micWorking.current) return;
    setToolStatus(null);
    stopAudio();
    setScanning(true);
    const startedAt = timestamp();
    logEvent("scan", `Camera ${cameraIndex} · calibration opened`);
    try {
      const result = await scanCamera(cameraIndex);
      const capturedAt = timestamp();
      if (result.capture_preview || !/scan_cancelled|no_camera_frames/.test(result.reason)) {
        setLastScan({ result, capturedAt, durationMs: capturedAt - startedAt });
      }
      logEvent(result.accepted ? "success" : "error", result.accepted ? `OCR accepted · ${result.text.trim().split(/\s+/).length} words` : `OCR rejected · ${result.reason}`);
      if (!result.accepted) {
        add({ role: "error", text: scanError(result.reason) });
        return;
      }
      setPageText(result.text);
      planCache.current = null;
      speechCache.current.clear();
      setPageMoods(null);
      setAskedTerms([]);
      setWordNote(null);
      setPageTitle("Scanned book page");
      setPageScanned(true);
      const reviewNote = result.openai_revision_status === "requested"
        ? " with AI transcription and revision"
        : result.openai_review_status === "requested" ? " with AI vision review" : "";
      add({ role: "notice", text: `Page scanned${reviewNote}. You can now ask LOOB about it.` });
    } catch (error) {
      logEvent("error", error instanceof LoobError ? error.message : "Camera scan failed");
      add({ role: "error", text: error instanceof LoobError ? error.message : "LOOB could not scan the camera page." });
    } finally {
      setScanning(false);
    }
  }

  async function toggleMic() {
    if (recording) { recorder.current?.stop(); return; }
    if (scanning || busy || micWorking.current) return;
    setToolStatus(null);
    stopAudio();
    if (!navigator.mediaDevices || typeof MediaRecorder === "undefined") { add({ role: "error", text: "This browser does not support microphone questions." }); return; }
    micWorking.current = true;
    setMicPending(true);
    try {
      const microphone = await navigator.mediaDevices.getUserMedia({ audio: true });
      const type = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg"].find(MediaRecorder.isTypeSupported);
      const mediaRecorder = type ? new MediaRecorder(microphone, { mimeType: type }) : new MediaRecorder(microphone);
      const chunks: BlobPart[] = [];
      stream.current = microphone;
      recorder.current = mediaRecorder;
      mediaRecorder.ondataavailable = (event) => event.data.size && chunks.push(event.data);
      mediaRecorder.onstop = async () => {
        microphone.getTracks().forEach((track) => track.stop());
        setRecording(false);
        const blob = new Blob(chunks, { type: mediaRecorder.mimeType || "audio/webm" });
        if (!blob.size) { add({ role: "error", text: "I did not hear anything. Please try again." }); return; }
        micWorking.current = true;
        setMicPending(true);
        try {
          const result = await transcribe(blob);
          micWorking.current = false;
          await submit(result.text, true);
        } catch (error) {
          add({ role: "error", text: error instanceof LoobError ? error.message : "LOOB could not transcribe that recording." });
        } finally {
          micWorking.current = false;
          setMicPending(false);
        }
      };
      mediaRecorder.start();
      setRecording(true);
    } catch {
      add({ role: "error", text: "Microphone permission is off. You can still type a question." });
    } finally {
      micWorking.current = false;
      setMicPending(false);
    }
  }

  useEffect(() => () => { stopAudio(); stream.current?.getTracks().forEach((track) => track.stop()); }, []);
  useEffect(() => { if (tab === "reader") journalEnd.current?.scrollIntoView({ block: "nearest", behavior: "smooth" }); }, [messages, tab]);
  function send(event: FormEvent) { event.preventDefault(); void submit(input); }
  const paragraphs = storyParagraphs(pageText);
  const controlsBusy = busy || micPending;
  const wordsAsked = countAskedWords(askedTerms);
  const journalMessages = messages.filter((message) => message.role === "user" || message.role === "assistant");
  const latestAnswer = messages.findLast((message) => message.role === "assistant");
  const wordCount = pageText.trim().split(/\s+/).length;
  const liveEyes = diagnostics.status === "connected" && diagnostics.data?.connected ? diagnostics.data : null;
  const eyeOpenness = liveEyes?.eyes_visible && liveEyes.openness
    ? Math.round((Math.min(1, liveEyes.openness.left) + Math.min(1, liveEyes.openness.right)) * 50) : null;

  function passageText(text: string) {
    const terms = [...new Set(askedTerms.map((term) => term.replace(/\s+/g, " ")))].sort((a, b) => b.length - a.length);
    if (!terms.length) return text;
    const pattern = terms.map((term) => term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|");
    return text.split(new RegExp(`(?<![\\p{L}\\p{N}])(${pattern})(?![\\p{L}\\p{N}])`, "giu")).map((part, index) => index % 2 ? <mark key={index}>{part}</mark> : part);
  }

  return (
    <main className="reading-workspace">
      <header className="desk-meta"><div className="app-logo-lockup" aria-label="LOOB"><Image className="app-logo-symbol" src={loobSymbol} alt="" priority /><span className="app-logo-name">LOOB</span></div></header>
      <div className="notebook">
        <div className="notebook-art" aria-hidden="true"><Image src={notebook} alt="" fill sizes="(max-width: 760px) 1600px, 1400px" placeholder="blur" preload /></div>
        <button
          id="view-bookmark"
          className="paper-bookmark"
          type="button"
          aria-label={tab === "reader" ? "Open Developer view" : "Return to Reader view"}
          aria-controls={tab === "reader" ? "developer-panel" : "reader-panel"}
          title={tab === "reader" ? "Open Developer view" : "Return to Reader view"}
          onClick={() => setTab((current) => current === "reader" ? "developer" : "reader")}
        >
          <Image src={bookmark} alt="" sizes="208px" draggable={false} />
          <span className="bookmark-label" aria-hidden="true">{tab === "reader" ? "Developer" : "Reader"} ↗</span>
        </button>

        <div id="reader-panel" className="notebook-spread" role="region" aria-label="Reader" hidden={tab !== "reader"}>
          <section className="notebook-page left-page journal-page" aria-label="Reading journal">
            <header className="page-heading"><span className="folio-heading">01</span><div><h1>{pageTitle}</h1><p className="page-subtitle">{sessionDate} · reading log</p></div></header>
            <div className="reader-mode"><span>Mode: <strong>{liveEyes?.mode === "SIGNAL" ? "Page signal" : liveEyes?.mode === "STOP" ? "Paused" : "Reading"}</strong></span>{liveEyes && <span className="mode-note">Eyes open: {eyeOpenness === null ? "—" : `${eyeOpenness}%`}</span>}</div>
            <div className="journal-toolbar">
              <h2 className="journal-heading">Voice log</h2>
              <button className={`icon-button reader-mic${recording ? " recording" : ""}`} type="button" disabled={!recording && (controlsBusy || scanning)} onClick={() => void toggleMic()} aria-label={recording ? "Stop recording" : "Start recording"} aria-pressed={recording} title={recording ? "Stop recording" : "Ask with your voice"}><DeskIcon name={recording ? "stop" : "mic"} width="19" height="19" /></button>
              {recording && <span className="listening-note" role="status">listening…</span>}
            </div>
            <form className="reader-composer" onSubmit={send}>
              <input value={input} onChange={(event) => setInput(event.target.value)} placeholder="Ask about this page…" aria-label="Question about the page" disabled={recording || controlsBusy || scanning} maxLength={2000} />
              <button className="icon-button" type="submit" aria-label="Send question" disabled={controlsBusy || recording || scanning || !input.trim()}><DeskIcon name="send" width="22" height="22" /></button>
            </form>
            {controlsBusy && <p className="reader-status" role="status">{micPending ? "Preparing your voice question…" : "Answering…"}</p>}
            {toolStatus && <p className={`reader-status${toolStatus.error ? " is-error" : ""}`} role={toolStatus.error ? "alert" : "status"}>{toolStatus.text}</p>}
            <div className="journal-lines">
              <div className="journal-messages" role="log" aria-label="Questions and answers" aria-live="polite" tabIndex={0}>
                {journalMessages.map((message) => <div key={message.id} className={`journal-entry ${message.role}`}>
                  <span>{message.role === "user" ? `“${message.text}”` : `(${message.text})`}</span>
                </div>)}
                <div ref={journalEnd} />
              </div>
            </div>
            <footer className="page-footer"><span>01 <span className="footer-title">{pageTitle}</span></span><span>Voice log</span></footer>
          </section>

          <section className="notebook-page right-page story-page" aria-label="Story">
            <header className="story-heading">
              <span className="page-kicker">{pageScanned ? "Scanned page" : "01 · Story"}</span>
              <div className="story-heading-actions"><span className="word-count">{wordCount} words</span><button className="notebook-button reader-read" type="button" onClick={() => speaking !== null ? stopAudio() : void narratePage(0, true)} disabled={scanning || recording || controlsBusy} aria-label={speaking !== null ? "Stop audio" : "Read story"}><DeskIcon name={speaking !== null ? "stop" : "play"} width="14" height="14" />{speaking !== null ? "Stop" : "Read"}</button></div>
            </header>
            <article className="story-text" aria-label="Story text" tabIndex={0}>
              {paragraphs.map((paragraph, index) => <p key={`${index}-${paragraph}`} className={`story-paragraph${activeParagraph === index ? " active" : ""}`}>{passageText(paragraph)}</p>)}
              {wordNote && <aside className="word-note" aria-label={`Meaning of ${wordNote.term}`}>
                <Image src={stickyNote} alt="" fill sizes="250px" aria-hidden="true" />
                <div className="word-note-content" tabIndex={0}><h2>{wordNote.term}</h2><p>{wordNote.answer}</p></div>
              </aside>}
            </article>
            <footer className="page-footer"><span>{wordsAsked} {wordsAsked === 1 ? "word" : "words"} asked</span><span>02</span></footer>
          </section>
        </div>

        <div id="developer-panel" className="notebook-spread" role="region" aria-label="Developer" hidden={tab !== "developer"}>
          <DeveloperPanel diagnostics={diagnostics} lastScan={lastScan} scanning={scanning} scanDisabled={controlsBusy || recording} cameraIndex={cameraIndex} onCameraIndexChange={setCameraIndex} onScan={() => void scanPage()} pageText={pageText} events={events}>
            <section className="reader-tools" aria-labelledby="reader-tools-heading">
              <h3 id="reader-tools-heading">Reader controls</h3>
              <div className="narration-actions">
                <button className="notebook-button" onClick={() => speaking !== null ? stopAudio() : void narratePage(0, true)} disabled={scanning || recording || controlsBusy}><DeskIcon name={speaking !== null ? "stop" : "play"} width="13" height="13" />{speaking !== null ? "Stop audio" : "Read page"}</button>
                {latestAnswer && <button className="text-button" onClick={() => void play(latestAnswer.text, latestAnswer.id)} disabled={scanning || recording || controlsBusy}>Replay answer</button>}
              </div>
              <div className="sound-settings">
                <label className="immersive-toggle"><input type="checkbox" checked={immersive} onChange={(event) => { stopAudio(); setImmersive(event.target.checked); }} /><span className="toggle-track" aria-hidden="true" /> Immersive narration</label>
                <label className="sound-volume"><DeskIcon name="volume" width="14" height="14" /><span className="sr-only">Immersive sounds volume</span><input type="range" min="0" max="100" value={ambientVolume} disabled={!immersive} onChange={(event) => { const value = Number(event.target.value); setAmbientVolume(value); ambience.current?.setVolume(value / 100); }} /><output>{ambientVolume}%</output></label>
              </div>
              {immersive && pageMoods && <p className="narration-moods">{pageMoods.join(" · ")}</p>}
              <form className="developer-composer" onSubmit={send}>
                <button className={`icon-button${recording ? " recording" : ""}`} type="button" disabled={!recording && (controlsBusy || scanning)} onClick={() => void toggleMic()} aria-label={recording ? "Stop recording" : "Start recording"}><DeskIcon name={recording ? "stop" : "mic"} width="18" height="18" /></button>
                <input ref={questionInput} value={input} onChange={(event) => setInput(event.target.value)} placeholder={recording ? "Recording…" : "Ask about this page"} aria-label="Question about the page" disabled={recording || controlsBusy || scanning} maxLength={2000} />
                <button className="icon-button" aria-label="Send question" disabled={controlsBusy || recording || scanning || !input.trim()}><DeskIcon name="send" width="19" height="19" /></button>
              </form>
              {scanning && <p className="tool-status" role="status">Camera window: C captures · Q cancels</p>}
              {controlsBusy && <p className="tool-status" role="status">{micPending ? "Transcribing…" : "Answering…"}</p>}
              {toolStatus && <p className={`tool-status${toolStatus.error ? " is-error" : ""}`} role={toolStatus.error ? "alert" : "status"}>{toolStatus.text}</p>}
            </section>
          </DeveloperPanel>
        </div>
      </div>
    </main>
  );
}
