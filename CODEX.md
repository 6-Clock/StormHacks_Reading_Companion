# LOOB development notes

The [root README](README.md) is the canonical setup and usage guide.
[backend/README.md](backend/README.md) describes API endpoints and capture behavior.
Read the installed Next.js guides required by [AGENTS.md](AGENTS.md) before frontend
changes; this workspace uses Next.js 16.3.8 and React 19.

## Architecture

- `src/components/ReadingDesk.tsx` owns shared Reader/Developer page state.
- `src/lib/voice-session.ts` manages the browser's GPT-Live WebRTC connection,
  transcript, narration requests, and hosted OCR delegation.
- `src/lib/api.ts` provides the minimal FastAPI client.
- `src/components/DeveloperPanel.tsx` and `LiveCameraPreview.tsx` show actual
  camera captures and eye-tracker diagnostics.
- `src/lib/scan-jobs.ts`, `diagnostics.ts`, and `tracker-settings.ts` connect those
  views to local capture and tracker state.
- `backend/app/main.py` bootstraps GPT-Live, uploads images, and exposes capture
  and tracker endpoints. Credentials stay on the server.
- `backend/app/services/camera_capture.py` contains OpenCV capture helpers;
  `book_scanner.py` uses them without model calls.
- `backend/app/services/scan_jobs.py` coordinates one cancellable camera worker
  and publishes the delegated model's accepted page result.
- `backend/computer vision/eyetracking_opencv.py` remains the separate native eye
  tracker; `book_ocr.py` is a small capture-only diagnostic.

GPT-Live uses `gpt-live-1` with a hosted Responses reasoning backend, defaulting to
`gpt-6.1-sol` with low reasoning effort and Fast processing. Spoken and typed
questions, narration, and OCR use the same browser
session. Captures can finish offline; transcription waits for a connected session.
The backend prompt can request another capture of the same unreadable page. That
recovery tool never repeats a physical page-turn command.

## Scope and behavior

The serial bridge waits for the MCU's matching ACK and DONE before page capture.
DONE follows the sequence's existing calibrated delays; it does not measure
physical servo position. Failed or uncertain commands are
not retried automatically. There is no turn reservation/commit handshake, layered
stage watchdogs, OCR confidence/revision chain, or duplicate-text suppression.
Keep actual camera release, cancellation, error reporting, and accepted-page
storage explicit. Physical transmission is not proof of mechanical completion.
Add new guards only when a reproducible failure establishes a need.

Reader keeps the notebook artwork, shared journal, typed and spoken conversation,
and Read/Stop controls. Developer keeps real captures and diagnostics. Word
highlights, word-box overlays, requested-word counts, answer sticky notes, immersive
modes, sound effects, speech caches, and separate provider pipelines are removed.
Fonts remain bundled locally, and pages stack on narrow displays.

## Validation

Use the README's backend pytest, frontend lint, and build commands.
`tests/browser/` contains mocked browser checks. These checks do not establish
provider account access, faithful spoken narration, OCR accuracy/latency, or real
camera/page-flipper behavior; validate those on the target setup.

Keep `.env`, dependencies, and generated captures out of Git. Do not commit,
push, or open a PR without the user's explicit request.
