"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { ask, LoobError, makeSpeech, planNarration, scanCamera, transcribe, type NarrationMood, type NarrationPlan, type StoryEffect } from "@/lib/api";
import { AmbientSound } from "@/lib/ambient";

type Message = { id: number; role: "user" | "assistant" | "notice" | "error"; text: string; voice?: boolean };

const initialPassage = [
  "At midnight, Mara stepped into the empty house. Footsteps sounded above her, though no one was supposed to be there. A shadow slid across the stairwell, and a whisper called her name.",
  "She followed the sound to the attic. The door creaked open before she touched it. In the dark, two pale eyes stared from behind a stack of boxes, and Mara held her breath.",
  "A small black cat padded into the moonlight and brushed against her ankle. Mara let out a laugh. The warm glow of a night-light filled the attic, and the house felt safe again.",
];
const prompts = ["Why was Mara afraid?", "What was in the attic?", "Read this paragraph to me."];

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
  const [messages, setMessages] = useState<Message[]>([
    { id: 1, role: "notice", text: "Page detected · The Whisper in the Attic" },
    { id: 2, role: "assistant", text: "Hi, I’m LOOB. Tap a paragraph to hear it, or ask me a question about the page." },
  ]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [recording, setRecording] = useState(false);
  const [speaking, setSpeaking] = useState<number | null>(null);
  const [pageText, setPageText] = useState(() => initialPassage.join("\n\n"));
  const [pageTitle, setPageTitle] = useState("The Whisper in the Attic");
  const [cameraIndex, setCameraIndex] = useState(1);
  const [scanning, setScanning] = useState(false);
  const [immersive, setImmersive] = useState(false);
  const [ambientVolume, setAmbientVolume] = useState(60);
  const [readingPage, setReadingPage] = useState(false);
  const [activeParagraph, setActiveParagraph] = useState<number | null>(null);
  const [pageMoods, setPageMoods] = useState<NarrationMood[] | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const messageSequence = useRef(2);
  const stream = useRef<MediaStream | null>(null);
  const audio = useRef<HTMLAudioElement | null>(null);
  const audioUrl = useRef<string | null>(null);
  const speechRequest = useRef<AbortController | null>(null);
  const playbackVersion = useRef(0);
  const ambience = useRef<AmbientSound | null>(null);
  const planCache = useRef<{ text: string; plan: NarrationPlan } | null>(null);
  const speechCache = useRef(new Map<string, Blob>());

  function add(message: Omit<Message, "id">) {
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
    setReadingPage(false);
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
    const paragraphs = storyParagraphs(pageText);
    if (!paragraphs.length) return;
    stopAudio();
    const version = playbackVersion.current;
    const controller = new AbortController();
    speechRequest.current = controller;
    setReadingPage(fullPage);
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
    if (!cleaned || busy) return;
    stopAudio();
    add({ role: "user", text: cleaned, voice: fromMic });
    setInput("");
    setBusy(true);
    try {
      const result = await ask(cleaned, pageText);
      const id = ++messageSequence.current;
      setMessages((items) => [...items, { id, role: "assistant", text: result.answer }]);
      void play(result.answer, id);
    } catch (error) {
      add({ role: "error", text: error instanceof LoobError ? error.message : "LOOB could not answer that question." });
    } finally {
      setBusy(false);
    }
  }

  async function scanPage() {
    if (scanning) return;
    stopAudio();
    setScanning(true);
    try {
      const result = await scanCamera(cameraIndex);
      if (!result.accepted) {
        add({ role: "error", text: scanError(result.reason) });
        return;
      }
      setPageText(result.text);
      planCache.current = null;
      speechCache.current.clear();
      setPageMoods(null);
      setPageTitle("Scanned book page");
      const reviewNote = result.openai_revision_status === "requested"
        ? " with AI transcription and revision"
        : result.openai_review_status === "requested" ? " with AI vision review" : "";
      add({ role: "notice", text: `Page scanned${reviewNote}. You can now ask LOOB about it.` });
    } catch (error) {
      add({ role: "error", text: error instanceof LoobError ? error.message : "LOOB could not scan the camera page." });
    } finally {
      setScanning(false);
    }
  }

  async function toggleMic() {
    if (recording) { recorder.current?.stop(); return; }
    if (!navigator.mediaDevices || typeof MediaRecorder === "undefined") { add({ role: "error", text: "This browser does not support microphone questions." }); return; }
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
        try {
          const result = await transcribe(blob);
          await submit(result.text, true);
        } catch (error) {
          add({ role: "error", text: error instanceof LoobError ? error.message : "LOOB could not transcribe that recording." });
        }
      };
      mediaRecorder.start();
      setRecording(true);
    } catch {
      add({ role: "error", text: "Microphone permission is off. You can still type a question." });
    }
  }

  useEffect(() => () => { stopAudio(); stream.current?.getTracks().forEach((track) => track.stop()); }, []);
  function send(event: FormEvent) { event.preventDefault(); void submit(input); }
  const paragraphs = storyParagraphs(pageText);

  return (
    <main className="shell">
      <header className="topbar">
        <div className="brand"><span className="brand-mark">L</span><span>LOOB <small>Reading Companion</small></span></div>
        <span className="status"><span className="dot" /> Voice ready</span>
      </header>
      <div className="desk">
        <section className="card">
          <header className="card-head"><div><p className="eyebrow">Today’s story</p><h1>{pageTitle}</h1></div><span className="page-number">Page 1</span></header>
          <div className="scan-controls">
            <label>Camera <input type="number" min="0" max="10" value={cameraIndex} onChange={(event) => setCameraIndex(Number(event.target.value) || 0)} disabled={scanning} /></label>
            <span className="ai-scan-note">AI vision OCR</span>
            <button className="scan-button" type="button" onClick={() => void scanPage()} disabled={scanning}>{scanning ? "Scanning…" : "Scan page"}</button>
          </div>
          <div className="narration-controls">
            <button className="read-page-button" type="button" onClick={() => activeParagraph !== null ? stopAudio() : void narratePage(0, true)} disabled={!paragraphs.length}>
              {activeParagraph !== null ? "Stop reading" : "Read page"}
            </button>
            <label className="immersive-switch"><input type="checkbox" checked={immersive} onChange={(event) => { stopAudio(); setImmersive(event.target.checked); }} /> Immersive narration</label>
            <label className="volume-control">Immersive sounds <input type="range" min="0" max="100" value={ambientVolume} disabled={!immersive} onChange={(event) => { const value = Number(event.target.value); setAmbientVolume(value); ambience.current?.setVolume(value / 100); }} aria-label="Immersive sounds volume" /></label>
          </div>
          <article className="reader">{paragraphs.map((paragraph, index) => <p key={`${index}-${paragraph}`}>
            {immersive && pageMoods?.[index] && <span className="mood-label">{pageMoods[index] === "suspense" ? "Suspense" : pageMoods[index] === "warm" ? "Warm" : "Neutral"}</span>}
            <button className={`sentence ${activeParagraph === index ? "active" : ""}`} aria-pressed={activeParagraph === index} onClick={() => activeParagraph === index && !readingPage ? stopAudio() : void narratePage(index, false)}>{paragraph}</button>
          </p>)}</article>
          <footer className="reader-help">Scan opens a calibration preview: align the page, press C to capture, or Q to cancel. Only accepted text replaces this page.</footer>
        </section>
        <section className="card chat">
          <header className="card-head"><div><p className="eyebrow">Ask about the page</p><h2>LOOB Companion</h2></div><span className="status">{speaking !== null ? "Speaking" : "Listening"}</span></header>
          <div className="messages" aria-live="polite">
            {messages.map((message) => <div key={message.id} className={`bubble ${message.role}`}>
              {message.voice && <span className="voice-label">You said</span>}
              {message.text}
              {message.role === "assistant" && <button className="replay" onClick={() => speaking === message.id ? stopAudio() : void play(message.text, message.id)}>{speaking === message.id ? "Stop" : "Replay"}</button>}
            </div>)}
            {busy && <div className="bubble notice">LOOB is thinking…</div>}
          </div>
          <div className="samples">{prompts.map((prompt) => <button key={prompt} onClick={() => prompt.startsWith("Read") ? void narratePage(0, false) : void submit(prompt)}>{prompt}</button>)}</div>
          {speaking !== null && <div className="audio-state">{activeParagraph !== null ? `Narrating paragraph ${activeParagraph + 1}${immersive ? " · immersive mode" : ""}` : "ElevenLabs voice is playing"}</div>}
          <form className="composer" onSubmit={send}>
            <button className={`icon-button ${recording ? "listening" : ""}`} type="button" onClick={() => void toggleMic()} aria-label={recording ? "Stop recording" : "Start recording"}>{recording ? "■" : "🎙"}</button>
            <input value={input} onChange={(event) => setInput(event.target.value)} placeholder={recording ? "Listening… tap stop when you are done" : "Ask about this page"} disabled={recording} />
            <button className="send" aria-label="Send question" disabled={busy}>↑</button>
          </form>
        </section>
      </div>
    </main>
  );
}
