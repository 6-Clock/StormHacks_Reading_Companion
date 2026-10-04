"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { ask, LoobError, makeSpeech, scanCamera, transcribe } from "@/lib/api";

type Message = { id: number; role: "user" | "assistant" | "notice" | "error"; text: string; voice?: boolean };

const initialPassage = [
  "Mina found the garden gate open just as the afternoon rain began to soften. A small rabbit stood beneath the arch, holding a silver key between its paws.",
  "“This key opens only one door,” said the rabbit. “Choose carefully, because the door you choose will show you what you are ready to learn.”",
  "Mina listened to the rain, then tucked the key safely into her pocket. She decided that understanding the question mattered before choosing an answer.",
];
const prompts = ["What does Mina learn?", "Explain the silver key.", "Read this paragraph to me."];

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

export function ReadingDesk() {
  const [messages, setMessages] = useState<Message[]>([
    { id: 1, role: "notice", text: "Page detected · The Garden Gate" },
    { id: 2, role: "assistant", text: "Hi, I’m LOOB. Tap a sentence to hear it, or ask me a question about the page." },
  ]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [recording, setRecording] = useState(false);
  const [speaking, setSpeaking] = useState<number | null>(null);
  const [pageText, setPageText] = useState(() => initialPassage.join("\n\n"));
  const [pageTitle, setPageTitle] = useState("The Garden Gate");
  const [cameraIndex, setCameraIndex] = useState(1);
  const [scanning, setScanning] = useState(false);
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const audio = useRef<HTMLAudioElement | null>(null);
  const audioUrl = useRef<string | null>(null);
  const speechRequest = useRef<AbortController | null>(null);

  function add(message: Omit<Message, "id">) {
    setMessages((items) => [...items, { ...message, id: Date.now() + items.length }]);
  }

  function stopAudio() {
    speechRequest.current?.abort();
    speechRequest.current = null;
    audio.current?.pause();
    if (audioUrl.current) URL.revokeObjectURL(audioUrl.current);
    audio.current = null;
    audioUrl.current = null;
    setSpeaking(null);
  }

  async function play(text: string, id: number) {
    stopAudio();
    const controller = new AbortController();
    speechRequest.current = controller;
    setSpeaking(id);
    try {
      const blob = await makeSpeech(text, controller.signal);
      if (controller.signal.aborted) return;
      const url = URL.createObjectURL(blob);
      const player = new Audio(url);
      audio.current = player;
      audioUrl.current = url;
      player.onended = () => stopAudio();
      player.onerror = () => { stopAudio(); add({ role: "notice", text: "Voice playback failed. You can tap Replay to try again." }); };
      await player.play();
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      stopAudio();
      add({ role: "notice", text: error instanceof LoobError ? error.message : "LOOB could not prepare that voice response." });
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
      const id = Date.now();
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
  const paragraphs = pageText.split(/\n\s*\n/).map((paragraph) => paragraph.trim()).filter(Boolean);

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
          <article className="reader">{paragraphs.map((paragraph, index) => <p key={`${index}-${paragraph}`}><button className="sentence" onClick={() => void play(paragraph, -(index + 1))}>{paragraph}</button></p>)}</article>
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
          <div className="samples">{prompts.map((prompt) => <button key={prompt} onClick={() => prompt.startsWith("Read") ? void play(paragraphs[0] ?? "", -1) : void submit(prompt)}>{prompt}</button>)}</div>
          {speaking !== null && <div className="audio-state">ElevenLabs voice is playing</div>}
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
