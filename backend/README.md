# LOOB backend

FastAPI bootstraps one GPT-Live WebRTC session, uploads captured images, and
coordinates camera capture and eye-tracker diagnostics. Audio connects directly
between the browser and OpenAI. Hosted Responses delegation handles questions,
narration requests, and OCR within that session; there is no separate STT, answer,
TTS, or OCR-revision pipeline.

## Run

From the repository root:

```powershell
uv sync --project backend --python 3.12
Set-Location backend
uv run uvicorn app.main:app --reload --port 8001
```

Create `backend/.env` with `OPENAI_API_KEY`. Optional values are `OPENAI_MODEL`
(default `gpt-6.1-sol`), `OPENAI_REASONING_EFFORT` (default `low`),
`OPENAI_SERVICE_TIER` (default `priority`, selecting Fast processing),
`OPENAI_VOICE` (default `marin`), and comma-separated
`ALLOWED_ORIGINS`. The voice frontend is `gpt-live-1`. Use one API worker because
capture jobs and tracker settings live in backend memory. The browser owns the
current accepted page.

Python dependencies and development tools are declared in `pyproject.toml` and
locked in `uv.lock`. Optional `--extra hardware` adds legacy MediaPipe Face Mesh
and pyserial for the eye tracker. Python 3.11/3.12 and NumPy 1.x support those
legacy binaries; the project uses one OpenCV contrib package for both capture
and tracking. No OpenAI SDK, Tesseract, or ElevenLabs packages are required.

## API

Open [interactive API documentation](http://127.0.0.1:8001/docs) for schemas.

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | API availability |
| `POST /v1/voice/session` | Exchange a browser SDP offer for a GPT-Live session and answer |
| `POST /v1/voice/images` | Upload a captured JPEG data URL and return its OpenAI file ID |
| `POST /v1/scan-jobs` | Start manual framing or immediate test/automatic capture |
| `GET /v1/scan-jobs/latest` | Latest capture job; optional `include_result=true` |
| `GET /v1/scan-jobs/{id}` | Job state; `include_result=true` includes capture/result data |
| `GET /v1/scan-jobs/{id}/preview` | Current manual-framing JPEG |
| `POST /v1/scan-jobs/{id}/capture` | Capture the manually framed page |
| `POST /v1/scan-jobs/{id}/cancel` | Release capture resources or cancel pending transcription |
| `POST /v1/scan-jobs/{id}/result` | Publish accepted text or a rejection from the delegated model |
| `GET/PATCH /v1/tracker-settings` | Read/change Blink-only mode and camera pause state |
| `POST /v1/tracker-settings/ack` | Tracker acknowledgement and actual camera state |
| `GET/POST /v1/diagnostics/eyes` | Read/publish eye-tracker measurements |

The old transcription, answer, speech, narration-plan, page-turn reservation,
and compatibility scan endpoints have been removed.

## Capture and model recovery

A physical page-turn command is followed by an immediate capture request. Manual
jobs show a framing preview until Capture or Cancel. Each capture worker owns and
releases its camera; the tracker releases its camera before another handle opens
and resumes after capture completes. One active camera capture is admitted at a
time. Cancel signals the worker and kills it if it does not exit during cleanup.

Captured JPEGs are kept in memory. The browser uploads the JPEG and supplies its
file ID to the session's delegated backend. Without a connected browser session,
capture can finish but OCR waits. Once the model accepts text, the browser posts
that result and updates its accepted page. Rejections preserve the previous page.
Cancelled or obsolete captures cannot replace it.

The backend system prompt tells the reasoning model to attempt OCR immediately,
request `capture_page` again for the same unreadable or moving page, and ask for
repositioning if recovery fails. The tool never sends a physical turn command.
There is no fixed settling delay, reservation handshake, stage watchdog ladder,
confidence threshold, separate revision pass, or duplicate-page-text suppression.
Actual tool outcomes and resource cleanup remain application responsibilities.

The tracker and serial setup commands are in the [root README](../README.md).
The standalone `computer vision/book_ocr.py` diagnostic only captures a JPEG;
it no longer transcribes text or opens a calibration window.

## Checks

From the repository root:

```powershell
uv run --project backend pytest
```

Tests use mock provider responses and synthetic capture workers. They do not
establish GPT-Live account access, narration fidelity, OCR latency or accuracy,
or physical camera/page-flipper operation. Validate those on the target hardware.
