"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { ask, LoobError, makeSpeech, transcribe } from "@/lib/api";

type Message = { id: number; role: "user" | "assistant" | "notice" | "error"; text: string; voice?: boolean };

const passage = [
  "Mina found the garden gate open just as the afternoon rain began to soften. A small rabbit stood beneath the arch, holding a silver key between its paws.",
  "“This key opens only one door,” said the rabbit. “Choose carefully, because the door you choose will show you what you are ready to learn.”",
  "Mina listened to the rain, then tucked the key safely into her pocket. She decided that understanding the question mattered before choosing an answer.",
];
const pageText = passage.join("\n\n");
const prompts = ["What does Mina learn?", "Explain the silver key.", "Read this paragraph to me."];

export function ReadingDesk() {
  const [messages, setMessages] = useState<Message[]>([
    { id: 1, role: "notice", text: "Page detected · The Garden Gate" },
    { id: 2, role: "assistant", text: "Hi, I’m LOOB. Tap a sentence to hear it, or ask me a question about the page." },
  ]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [recording, setRecording] = useState(false);
  const [speaking, setSpeaking] = useState<number | null>(null);
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

  return (
    <main className="shell">
      <header className="topbar">
        <div className="brand"><span className="brand-mark">L</span><span>LOOB <small>Reading Companion</small></span></div>
        <span className="status"><span className="dot" /> Voice ready</span>
      </header>
      <div className="desk">
        <section className="card">
          <header className="card-head"><div><p className="eyebrow">Today’s story</p><h1>The Garden Gate</h1></div><span className="page-number">Page 1</span></header>
          <article className="reader">{passage.map((sentence, index) => <p key={sentence}><button className="sentence" onClick={() => void play(sentence, -(index + 1))}>{sentence}</button></p>)}</article>
          <footer className="reader-help">Tap any paragraph to hear it in your selected ElevenLabs voice.</footer>
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
          <div className="samples">{prompts.map((prompt) => <button key={prompt} onClick={() => prompt.startsWith("Read") ? void play(passage[0], -1) : void submit(prompt)}>{prompt}</button>)}</div>
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
