# StormHacks Reading Companion

## Project summary

LOOB is a local reading companion. It displays scanned text, answers reader questions, accepts spoken questions, and narrates passages or answers.

## Architecture

- `src/` — Next.js 16 / React 19 interface.
- `src/components/ReadingDesk.tsx` — shared reading state, Reader journal/story, question and narration controls in both views, and Developer scan/replay/immersive settings.
- `src/components/DeveloperPanel.tsx` — notebook appendix with eye diagram, live book-camera framing, page-turn countdown, OCR results, and real event logs.
- `src/components/LiveCameraPreview.tsx` — bounded, fresh camera-image polling while Developer is visible; clears the image when polling stops.
- `src/lib/diagnostics.ts` — eye diagnostics polling with hidden-tab suspension and offline retry.
- `src/lib/scan-jobs.ts` — bounded scan polling, per-job results, capture/cancel actions, skipped busy requests, and reconnect recovery.
- `src/lib/tracker-settings.ts` — desired blink-only setting and live tracker acknowledgement.
- `src/lib/reading-journal.ts` — conservative asked-term extraction against page text and distinct asked-word counting.
- `src/assets/` — supplied notebook/bookmark artwork, design references, and self-hosted fonts with licenses.
- `src/lib/api.ts` — browser client for the FastAPI service.
- `backend/app/` — FastAPI endpoints and provider configuration.
- `backend/app/services/book_scanner.py` — live browser framing, camera capture, and AI OCR adapter using the same camera handle.
- `backend/app/services/scan_jobs.py` — shared reservation/scan gate and supervised worker processes.
- `backend/app/services/tracker_control.py` — nonblocking tracker settings, reservation, serial dispatch, and scan coordination.
- `backend/app/services/eye_publisher.py` — background HTTP diagnostics sender with a single replaceable pending snapshot.
- `backend/computer vision/blink_detector.py` — calibrated relative-eyelid blink detection shared with pipeline tests.
- `backend/computer vision/book_ocr.py` — standalone camera OCR program and image-processing helpers.
- `backend/computer vision/eyetracking_opencv.py` — eye-tracking prototype.

## Services and endpoints

- `GET /health` and `GET /api/v1/health` — service health checks.
- `POST /v1/scan-jobs` — starts a manual, test, or automatic OCR job when idle (`202` with its job ID); skips conflicting requests (`409`) without saving them for later.
- `GET /v1/scan-jobs/latest` and `GET /v1/scan-jobs/{id}?include_result=true` — progress and a specific completed result.
- `GET /v1/scan-jobs/{id}/preview` — current manual-framing JPEG and capture timestamp, or a null frame when absent, stale, or no longer framing.
- `POST /v1/scan-jobs/{id}/capture` and `/cancel` — browser capture and cancellation.
- `POST /v1/page-turns/reserve` and `/v1/page-turns/{id}/commit` — reserve before serial dispatch, then settle and scan.
- `GET/PATCH /v1/tracker-settings` and `POST /v1/tracker-settings/ack` — desired controls and acknowledgement from the tracker camera loop.
- `POST /v1/scan-camera` and `/v1/auto-scans` — compatibility routes returning jobs through the same coordinator.
- `POST /v1/ask` — answers a question using the active page text.
- `POST /v1/transcribe` — converts a recorded question to text with ElevenLabs.
- `POST /v1/speech` — creates spoken narration with ElevenLabs.
- `POST /v1/narration-plan` — classifies story paragraphs for immersive narration.
- `POST /v1/diagnostics/eyes` — accepts bounded measurements from the acknowledged tracker session.
- `GET /v1/diagnostics/eyes` — returns the latest validated measurements from backend memory; disconnected when absent or stale.

Provider keys stay in `backend/.env`; never commit that file.

## Notebook frontend

The green bookmark switches between **Reader** and **Developer**, sharing the same reading state. It supports click, Enter, and Space. LOOB appears above the notebook on a solid `#2D2D2D` background. Reader contains a lined journal of actual questions and answers on the left and 16px story text on the right. The journal starts empty, without a greeting or suggested prompts, and uses Architects Daughter handwriting. Reader shows **Mode: Reading**, a distinct asked-word count, yellow highlights on asked terms, and a sticky note containing an actual answer. Story paragraphs have no playback click action. The suspense demo is preserved.

Reader has a microphone beside **Voice log**, a typed question input and send button beneath it, and **Read/Stop** in the story header. These controls share draft, recording, submission, and playback state with Developer. Answer replay, immersive sound settings, and scanning remain in Developer. The appendix retains actual scan photos and OCR results and displays live iris direction, relative openness, timing, and real tracker/session events. Eye data is never simulated; camera disconnection clears measurements. Optional local Tesseract supplies actual word boxes with normalized image coordinates for the scan overlay; AI text remains authoritative. Pages stack on small screens. Fonts are bundled locally so production builds do not fetch Google Fonts.

The standalone tracker sends derived measurements to FastAPI at most five times per second through `app/services/eye_publisher.py`. A dedicated background worker keeps one latest pending snapshot and one request in flight; camera processing never waits for HTTP. The backend retains measurements in memory under the acknowledged tracker session and rejects old sessions/sequences. Original capture timestamps determine freshness; retries cannot extend it. Data expires after two seconds. Live diagnostics no longer read or replace `backend/.runtime/eyes.json`, eliminating that Windows file-lock conflict. The browser polls at 500 ms when live, retries disconnected measurements after two seconds, and clears expired measurements even during a stalled request. It suspends polling in hidden tabs. Camera startup and calibration remain in the native tracker window. Eye tracking and book scanning use different cameras sequentially: the eye camera closes before the book camera opens, then resumes after the scan guard clears.

Developer shows why real blink detection is paused (calibration, missing eyes, READ gating, or active scan/control guard). The last actual `Blink 3/3 recorded` event remains visible after the live counter resets. The simulated test button does not create that camera confirmation. Camera eyelid math lives in `computer vision/blink_detector.py` and is tested together with the real read-mode controller using measured-openness sequences; those tests do not replace a physical camera check.

## AI OCR workflow

1. In Developer, select the book camera index and click **Live camera** or **Scan OCR**. Both create a manual job immediately, pause any running eye tracker, and wait for exclusive camera access before opening the live view inside the app. Manual scans work without the native tracker. Manual scans, simulated blinks, and real page turns share one ownership gate.
2. Center the page in the live view's guide, then choose **Capture now**. **Cancel scan** stops the job and framing expires after 60 seconds. The web workflow uses no native framing window. Opening the live preview does not require OpenAI configuration; OCR after capture does.
3. OpenCV selects a sharp frame and perspective-corrects it. The scan photo is retained for the Developer preview; optional local Tesseract provides normalized word boxes for its overlay.
4. OpenAI vision transcribes any legible visible text, including book text, handwriting, whiteboards, signs, notes, and partial text. People and scenery are ignored.
5. When first-pass confidence is below 92%, a second image-grounded AI revision verifies the transcript. A clear first pass skips this extra call.
6. Accepted AI text replaces the reading page; questions and narration use the new text. Local word-box text never replaces the AI transcript, and missing boxes are not simulated.

Each camera job runs in an isolated process. Camera, framing, provider, preview, and overall deadlines prevent indefinite waits. Cancellation requests cooperation first, then kills an unresponsive worker after a grace period. The coordinator releases ownership after the worker exits and any physical settling guard ends. Results remain associated with their job ID; cancelled or superseded results cannot replace the current page. Run one API process (no multiworker deployment) for this local hardware coordinator.

Manual framing emits bounded JPEGs at most five times per second from the camera handle later used for capture. The coordinator retains only the latest image in memory and expires it after two seconds using capture age and a monotonic deadline. Developer polls its job-specific preview at most four times per second. Preview polling stops in Reader and hidden browser tabs; the camera job itself remains subject to cancellation and the framing timeout. Images clear on capture, cancellation, terminal states, staleness, and preview unmount. Completed OCR photographs are separate from these live frames.

Windows book-camera opening uses DirectShow only. A bounded camera-2 diagnostic received frames with DirectShow, while MSMF stalled inside opening; this supports removing that fallback without establishing the cause of every earlier camera timeout. The standalone `book_ocr.py` program retains its native preview and keyboard controls.

### Page-turn timing and scan ownership

Current camera setup: `--camera 1 --ocr-camera 2` (eyes on `1`, book on `2`). The eye camera opens first. Three real blinks within two seconds obtain a 15-second reservation; the native loop then fully releases the eye camera before the control worker sends `flip right`, waits for matching MCU ACK and DONE, and commits the job. The **eight-second settling timer starts at commit**, not reservation or camera release. No OCR worker or book-camera capture starts before the full delay expires. Without `--port`, serial commands are terminal previews and no turn is committed. DONE confirms completion of the timed firmware sequence, without measured PWM-servo positions.

The serial connection is bidirectional: USB-to-TTL TXD connects to STM32 USART1 PB7/RX, adapter RXD connects to PA9/TX, and GND connects to GND with compatible 3.3 V logic. The board's Type C UART2-labelled connector exposes this USART1 link. The tracker sends `50 <request_id>\n` over `115200` baud, 8N1, with no flow control, and waits for matching `ACK <request_id>` followed by `DONE <request_id>`. It ignores numeric/offline telemetry and unrelated IDs. `BUSY`, `ERROR`, timeout and shutdown prevent OCR commit; the host does not retry motion. `--serial-completion-timeout` defaults to ten seconds. DONE is sent when the existing timed sequence enters state 6; physical servo positions are unverified. A second terminal cannot open the same COM port while LOOB owns it. Full protocol details and timing constants are in `autobook_embedded/BOOK_UART_SEQUENCE_README.txt`.

- Automatic scans, reserved page turns, and simulated blink tests have a server-enforced minimum of eight seconds, including requests from older clients that still send `1.5` seconds. The tracker defaults to eight seconds and accepts `--ocr-settle-seconds` values from 8 to 30.
- Manual **Live camera** and **Scan OCR** have no settling delay, but wait for eye-camera release when needed. They open browser framing and wait for **Capture now**. **Test 3 blinks** works without the native tracker or page-flipper hardware, waits eight seconds after its simulated sequence, then captures with the selected OCR camera without sending a hardware command.
- Tracker settings expose the active `camera_pause_job_id`. The native main loop processes this request even when no frames are available, keeps control heartbeats alive while paused, and acknowledges release only after closing the camera. Before starting the OCR worker, the coordinator waits up to ten seconds for explicit release acknowledgement. The worker then waits up to ten seconds for the OS lease before opening the book camera. Both waits report `waiting_for_eye_camera` and have a 15-second stage watchdog.
- The backend uses a monotonic deadline. During `settling`, the response includes `scan_starts_at` (Unix seconds) for Developer's visible countdown; that field disappears when settling ends. The browser countdown never starts a scan itself. The settling-stage timeout is 35 seconds, allowing the maximum configured wait to finish.
- All scan entry points share one exclusive coordinator. New requests during reservation, settling, capture, OCR, or cancellation receive `409` immediately; they are not retained or replayed. Admission also skips requests when the coordinator lock is busy. Repeated IDs for an existing job remain idempotent.
- Scan and blink-test controls are disabled while busy. A conflicting start received between status polls becomes a skipped notice, not an automatic retry. The tracker discards blocked gestures and requires a fresh one after the guard clears. API loss blocks physical commands.
- Ownership is released only after worker exit and any physical settling guard. Cancellation during a reserved turn preserves the guard through the reservation lease plus settling; cancellation after commit preserves the remaining settling delay. The active pause clears after that guard, and the tracker reopens its eye camera with cached frames and partial gestures discarded. Completion, failure, or cancellation never starts a skipped request.

The actual camera owners hold one OS file lease at `backend/.runtime/cameras.lock` before any camera open. Windows uses `msvcrt` locking. The eye reader, scan worker, and standalone OCR program participate, so an API restart cannot free a lease still held by a camera-owning process. Never delete, replace, or truncate the lock file to recover a busy camera. Close external camera apps and restart older tracker processes that do not participate in the protocol. Keep one API process for job coordination even with this OS lease.

If the tracker crashes without explicitly acknowledging camera release, close its process and restart FastAPI before manual scanning. An expired tracker heartbeat is not proof of camera release and does not clear the release requirement.

Restart FastAPI after backend changes unless using `--reload`, and restart the native tracker after tracker changes. The updated backend enforces the minimum delay even for a tracker that has not yet restarted. API restart clears in-memory jobs, diagnostics, and the blink-only setting.

The standalone `book_ocr.py` diagnostic keeps its separate 1.5-second placeholder flip wait. The eight-second policy is enforced by the app's shared scan coordinator. The standalone program acquires the camera lease but does not request tracker pause; it reports a busy error if another owner holds the lease. Stop the eye tracker first or use Developer's manual workflow for an automatic handoff.

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
python ".\backend\computer vision\book_ocr.py" --camera 2
```

Use `--local-ocr` only for the legacy Tesseract troubleshooting path. The eye-tracking prototype runs with:

```powershell
python ".\backend\computer vision\eyetracking_opencv.py" --camera 1 --ocr-camera 2
```

For eye tracking alone, omit `--ocr-camera` but keep FastAPI running for control coordination:

```powershell
python ".\backend\computer vision\eyetracking_opencv.py" --camera 1
```

For lower-resolution cameras, the tracker continuously captures the newest webcam frame and processes a 640px-wide image at most, reducing stale-frame latency. Press `C` while looking normally at the camera and keeping your eyes open for one second; it records per-eye median baselines. The preview shows capture and inference FPS, frame age, raw and normalized eyelid openness, and the `OPEN`/`CLOSING`/`CLOSED`/`REOPENING` phase. Use `--blink-sensitivity high` when partial blinks still appear open, and `--diagnostics-csv .\blink-diagnostics.csv` to save local timing/landmark measurements without video frames.

Calibrate with `C` before gesture detection. Normally a three-second gaze enters `READ`; the first confirmed blink starts a fixed two-second window, and two more blinks request one page turn. The controller enters `STOP` after the brief signal. Developer's **Blink-only test mode** bypasses the gaze/READ requirement, so fresh triple blinks work again after the scan/settling guard clears. Switching modes clears partial gestures. The switch reports pending/offline until the camera loop acknowledges the setting; it defaults off after API restart. Missing eyes, calibration, active scans, and lost API connection continue to inhibit page turns. There is no down-gaze gesture.

## Verification

- Sequential camera handoff was checked with real hardware: eye camera 1 streamed at about 30 FPS, released for camera 2's fresh 640x360 Developer preview, then reopened after cancellation. Manual OCR preview also worked after the tracker exited. Both cameras were closed after verification. These checks did not send a hardware page-turn command or call the OCR provider; a fresh physical three-blink sequence was not rechecked in this update.
- No tests were added or run for the sequential-camera change. Python syntax, scoped backend Ruff, TypeScript, scoped frontend ESLint, and whitespace checks passed. The native vision files retain existing long-line lint warnings.
- Latest scan-control checks: 27 scan-job tests pass, including commit-based eight-second timing, legacy-client minimums, immediate manual starts, and skipped requests that never replay.
- Tracker-control tests cover busy gesture rejection and fresh gestures after completion. Blink pipeline tests exercise calibrated measurements through the real mode controller.
- `tests/browser/scan-controls.js` verifies the countdown, disabled controls, manual capture, cancellation, reconnection, and skipped starts with mocked API responses. Reload after running it to restore live data. `tests/browser/eye-diagnostics.js` covers diagnostics readiness and freshness.
- TypeScript, scoped frontend lint, scoped backend Ruff, and tracker syntax checks pass for the timer update. The Next.js production build passed before this update; it was not rerun for the timer-only change.
- Live camera checks require local hardware; OCR and other provider calls additionally require valid keys and the appropriate model.

## Pre-push checklist

- Do not commit `.env`, `.venv`, `node_modules`, or camera diagnostics.
- Confirm `git diff --check` is clean.
- Run `python -m pytest` and `python -m ruff check .` from `backend` when the virtual environment is available.
- Review `git status` before staging files.
