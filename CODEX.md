# StormHacks Reading Companion

## Project summary

LOOB is a local reading companion. It displays scanned text, answers reader questions, accepts spoken questions, and narrates passages or answers.

## Architecture

- `src/` — Next.js 16 / React 19 interface.
- `src/components/ReadingDesk.tsx` — shared reading state, Reader journal/story, question and narration controls in both views, and Developer scan/replay/immersive settings.
- `src/components/DeveloperPanel.tsx` — notebook appendix with eye diagram, OCR results, and real event logs.
- `src/lib/diagnostics.ts` — eye diagnostics polling with hidden-tab suspension and offline retry.
- `src/lib/reading-journal.ts` — conservative asked-term extraction against page text and distinct asked-word counting.
- `src/assets/` — supplied notebook/bookmark artwork, design references, and self-hosted fonts with licenses.
- `src/lib/api.ts` — browser client for the FastAPI service.
- `backend/app/` — FastAPI endpoints and provider configuration.
- `backend/app/services/book_scanner.py` — camera calibration and AI OCR adapter.
- `backend/computer vision/book_ocr.py` — standalone camera OCR program and image-processing helpers.
- `backend/computer vision/eyetracking_opencv.py` — eye-tracking prototype.

## Services and endpoints

- `GET /health` and `GET /api/v1/health` — service health checks.
- `POST /v1/scan-camera` — opens an OpenCV calibration window and returns scanned text.
- `POST /v1/ask` — answers a question using the active page text.
- `POST /v1/transcribe` — converts a recorded question to text with ElevenLabs.
- `POST /v1/speech` — creates spoken narration with ElevenLabs.
- `POST /v1/narration-plan` — classifies story paragraphs for immersive narration.
- `GET /v1/diagnostics/eyes` — reads a bounded, validated local eye-tracker snapshot; returns disconnected when absent or stale.

Provider keys stay in `backend/.env`; never commit that file.

## Notebook frontend

The green bookmark switches between **Reader** and **Developer**, sharing the same reading state. It supports click, Enter, and Space. LOOB appears above the notebook on a solid `#2D2D2D` background. Reader contains a lined journal of actual questions and answers on the left and 16px story text on the right. The journal starts empty, without a greeting or suggested prompts, and uses Architects Daughter handwriting. Reader shows **Mode: Reading**, a distinct asked-word count, yellow highlights on asked terms, and a sticky note containing an actual answer. Story paragraphs have no playback click action. The suspense demo is preserved.

Reader has a microphone beside **Voice log**, a typed question input and send button beneath it, and **Read/Stop** in the story header. These controls share draft, recording, submission, and playback state with Developer. Answer replay, immersive sound settings, and scanning remain in Developer. The appendix retains actual scan photos and OCR results and displays live iris direction, relative openness, timing, and real tracker/session events. Eye data is never simulated; camera disconnection clears measurements. Optional local Tesseract supplies actual word boxes with normalized image coordinates for the scan overlay; AI text remains authoritative. Pages stack on small screens. Fonts are bundled locally so production builds do not fetch Google Fonts.

The standalone tracker automatically writes `backend/.runtime/eyes.json` at most five times per second. FastAPI reads that file through `app/services/eye_telemetry.py`, marks measurements stale after two seconds, and returns no camera images. The browser polls at 500 ms when live and five seconds when offline, suspending requests in hidden browser tabs. Camera startup and calibration remain in the native tracker window. Use different cameras for simultaneous eye tracking and book scanning.

## AI OCR workflow

1. In Developer, the reader selects a camera index and clicks **Scan**. Web scans are manual; an eye-triggered page flip does not automatically start OCR.
2. The backend opens **LOOB OCR calibration**. Center the target inside the yellow guide, press `C` to capture, or `Q`/Escape to cancel.
3. OpenCV selects a sharp frame and perspective-corrects it. The scan photo is retained for the Developer preview; optional local Tesseract provides normalized word boxes for its overlay.
4. OpenAI vision transcribes any legible visible text, including book text, handwriting, whiteboards, signs, notes, and partial text. People and scenery are ignored.
5. When first-pass confidence is below 92%, a second image-grounded AI revision verifies the transcript. A clear first pass skips this extra call.
6. Accepted AI text replaces the reading page; questions and narration use the new text. Local word-box text never replaces the AI transcript, and missing boxes are not simulated.

## Immersive narration

In Developer, the reader can enable **Immersive narration** and choose **Read page**. `POST /v1/narration-plan` labels each paragraph neutral, warm, or suspenseful and returns sentence-indexed cues for literal door creaks, footsteps, thunder, and knocks. OpenAI plans the scene; local rules provide moods and cues when it is unavailable. Paragraphs with cues are narrated in shorter sentence clips so each one-shot effect begins with the matching sentence. The browser requests ElevenLabs speech and prepares the next segment during playback. Optional warm and suspense voice IDs provide distinct voices; missing IDs use the default voice with different delivery settings. Browser-generated ambience crossfades with the mood, while one-shot effects share the **Immersive sounds** volume slider. Stopping narration stops every audio layer, and a new scan clears the page's mood and speech cache.

The Uvicorn terminal logs each AI OCR model, reason, acceptance result, and confidence. Close any other app holding the selected camera before scanning.

## Configuration

Required for the reading app:

```env
OPENAI_API_KEY=...
OPENAI_MODEL=...
ELEVENLAB_API=...
ELEVENLAB_VOICE_ID=...
ELEVENLAB_VOICE_ID_WARM=...      # Optional distinct voice for warm paragraphs
ELEVENLAB_VOICE_ID_SUSPENSE=...  # Optional distinct voice for suspenseful paragraphs
```

Optional OCR settings:

```env
OPENAI_OCR_MODEL=...        # Falls back to OPENAI_MODEL
OPENAI_OCR_REVIEW_MODEL=... # Falls back to OPENAI_OCR_MODEL
ALLOWED_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
```

## Local commands

From the repository root:

```powershell
py -3.11 -m venv backend\.venv
.\backend\.venv\Scripts\Activate.ps1
python -m pip install -r backend\requirements.txt
npm install
```

Run the backend from `backend`:

```powershell
python -m uvicorn app.main:app --reload --port 8001
```

Run the frontend from the repository root:

```powershell
npm run dev
```

Standalone AI OCR:

```powershell
python ".\backend\computer vision\book_ocr.py" --camera 1
```

Use `--local-ocr` only for the legacy Tesseract troubleshooting path. The eye-tracking prototype runs with:

```powershell
python ".\backend\computer vision\eyetracking_opencv.py" --camera 1
```

For lower-resolution cameras, the tracker continuously captures the newest webcam frame and processes a 640px-wide image at most, reducing stale-frame latency. Press `C` while looking normally at the camera and keeping your eyes open for one second; it records per-eye median baselines. The preview shows capture and inference FPS, frame age, raw and normalized eyelid openness, and the `OPEN`/`CLOSING`/`CLOSED`/`REOPENING` phase. Use `--blink-sensitivity high` when partial blinks still appear open, and `--diagnostics-csv .\blink-diagnostics.csv` to save local timing/landmark measurements without video frames.

In `READ` mode, the first confirmed blink starts a fixed 2-second window; two additional blinks before it ends emit `flip right`, and later blinks do not extend the deadline. The detector accepts full closure or a rapid eyelid-relative drop. The terminal prints the third blink before it sends `flip right`. Counts are ignored outside `READ` mode and during the short post-flip signal, so `3/3` always maps to `flip right`. There is no down-gaze gesture.

## Verification

- Next.js production build passes.
- Modified Python modules pass syntax checks.
- Live camera and provider calls require the local hardware, valid keys, and a vision-capable model.

## Pre-push checklist

- Do not commit `.env`, `.venv`, `node_modules`, or camera diagnostics.
- Confirm `git diff --check` is clean.
- Run `python -m pytest` and `python -m ruff check .` from `backend` when the virtual environment is available.
- Review `git status` before staging files.
