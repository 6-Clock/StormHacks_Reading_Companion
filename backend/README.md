# LOOB backend

This FastAPI service keeps OpenAI and ElevenLabs credentials on the server and exposes the reading-companion API.

## Setup and run

From the repository root:

```powershell
py -3.11 -m venv backend\.venv
.\backend\.venv\Scripts\Activate.ps1
python -m pip install -r backend\requirements.txt
Set-Location backend
python -m uvicorn app.main:app --reload --port 8001
```

Create `backend/.env` with at least `OPENAI_API_KEY`, `OPENAI_MODEL`, `ELEVENLAB_API`, and `ELEVENLAB_VOICE_ID` for the provider-backed features. Optional OCR-specific models are `OPENAI_OCR_MODEL` and `OPENAI_OCR_REVIEW_MODEL`. The manual live camera view can open without provider keys; running OCR after capture requires OpenAI configuration.

For immersive narration, optionally set `ELEVENLAB_VOICE_ID_WARM` and `ELEVENLAB_VOICE_ID_SUSPENSE` to distinct ElevenLabs voices. `POST /v1/narration-plan` returns validated paragraph moods, sentence boundaries, and up to two literal-event sound cues per paragraph. Approved cues are `door_creak`, `footsteps`, `thunder`, and `knock`. `POST /v1/speech` accepts an optional `mood` (`neutral`, `warm`, or `suspense`) and uses the matching configured voice; missing optional voices fall back to `ELEVENLAB_VOICE_ID`.

Open [http://127.0.0.1:8001/docs](http://127.0.0.1:8001/docs) for the API documentation. Health checks are available at `/health` and `/api/v1/health`.

## Live developer diagnostics

Start the eye tracker separately from the repository root, while FastAPI is running:

```powershell
python ".\backend\computer vision\eyetracking_opencv.py" --camera 1 --ocr-camera 2
```

Camera `1` tracks eyes and camera `2` scans the book, with exclusive camera
ownership passed between them. The eye tracker opens first and keeps its
native preview and controls: `C` calibrates open eyes, `R` resets, and `Q` or Escape
stops it. Calibrate first. Three confirmed blinks within two seconds obtain a
15-second page-turn reservation. The tracker fully releases the eye camera
before sending `flip right`, then commits an OCR job. The job waits
8 seconds for the physical page to settle before capturing without a calibration
window. After the brief signal, the tracker enters `STOP`. Normally, look away and
hold your gaze for three seconds to resume. Developer's **Blink-only test mode**
bypasses that gaze requirement; new blinks remain blocked until scanning/settling
ends. Changing the setting clears partial gestures. Change the wait with
`--ocr-settle-seconds` (8–30 seconds); omit `--ocr-camera` to disable automatic OCR. FastAPI is
still required for coordinated turns without OCR. Camera indexes must differ.

### STM32 USB-to-TTL UART output

Pass the adapter's COM port to the tracker, for example:

```powershell
python ".\backend\computer vision\eyetracking_opencv.py" --camera 1 --ocr-camera 2 --port COM3 --baud 115200
```

The tracker opens a TX-only UART link at 115200 8N1 with no flow control. After an accepted three-blink page turn, it maps its internal `flip right` action to the LF-terminated ASCII frame `50\n` (`0x35 0x30 0x0A`). Wire USB-to-TTL **TXD** to the STM32's selected USART **RX**, and connect both grounds. Use a 3.3 V logic-level adapter; leave the adapter RX and STM32 TX disconnected for this one-way setup. The tracker terminal prints each actual payload as `[UART TX ...]`; a second serial console cannot open the same COM port concurrently.

For eye tracking alone, keep FastAPI running and use
`python ".\backend\computer vision\eyetracking_opencv.py" --camera 1`.
Manual scans and simulated blink scans also work without a running eye tracker.
When it is running, they request its camera release and resume tracking after
the worker exits and the scan guard ends. Resuming clears partial gestures.

`GET/PATCH /v1/tracker-settings` reads/changes the desired `blink_only` boolean.
The tracker polls in a background thread, applies it in the camera loop, and
posts `/v1/tracker-settings/ack` with the revision and its session ID. The UI
reports applied only while that acknowledgement is fresh (three seconds). The
default is off on API restart; lost API connectivity blocks physical commands.
The settings response also exposes the active `camera_pause_job_id`. The native
loop handles camera release and resume even without new frames; control
heartbeats continue while eye measurements are intentionally paused.

The tracker uploads to `POST /v1/diagnostics/eyes` using its acknowledged
`tracker_session_id`, an increasing snapshot `sequence`, and a `snapshot` containing
measurements plus the original capture time in `updated_at`. The request body is
limited to 32 KiB. One dedicated background sender publishes at most five times
per second and retains only the newest pending snapshot. Camera processing and
the separate control/serial worker never wait for diagnostics uploads.

`GET /v1/diagnostics/eyes` returns the latest validated snapshot from backend
memory. Session ownership, sequence checking, and replacement use the same lock as
tracker acknowledgements. Stale, duplicated, and previous-session packets cannot
renew freshness; changing the desired blink setting keeps the current session.
Diagnostics do not renew the control heartbeat. Freshness uses the capture age at
receipt and a monotonic two-second expiry, and a backend restart starts offline.
No live diagnostics code reads or replaces `backend/.runtime/eyes.json`.

The response contains `connected`, `updated_at`, `camera_index`, `mode`,
`eyes_visible`, `gaze`, `openness`, `phase`, `blink_count`, `look_progress`,
`capture_fps`, `inference_fps`, `frame_age_ms`, `calibrated`, `turns_blocked`,
`blink_only`, and `events`. Timestamps use Unix
seconds. Gaze coordinates describe iris position within the eyes from 0 to 1;
they are an estimated direction, not a calibrated screen or book position.
Openness is relative to the open-eye baseline (1 means baseline openness and can
be exceeded). Gaze and openness are null when eyes are not visible.

If the tracker is stopped, unavailable, or has not updated for two seconds,
`connected` is false and live measurements are cleared. The last valid timestamp,
camera index, and recent events can remain available. Up to 32 real events record
mode changes, blinks, calibration, resets, and serial commands. Preview commands
are explicitly labeled; no hardware completion acknowledgment is inferred.
An API connection alone does not mean the camera tracker is connected.

## Cancellable OCR jobs and page turns

- `POST /v1/scan-jobs`: `{source: "manual"|"test"|"automatic", camera_index, eye_camera_index?, trigger_id?, settle_seconds?}` returns `202` with a `job_id`.
- `GET /v1/scan-jobs/latest` returns compact stage, timestamps, and events. Fetch a completed result from `GET /v1/scan-jobs/{id}?include_result=true`.
- `GET /v1/scan-jobs/{id}/preview` returns `{job_id, status, camera_index, frame}`; a fresh manual-framing `frame` contains a JPEG data URL, dimensions, and `captured_at`. Other stages or expired images return `frame: null`.
- `POST /v1/scan-jobs/{id}/capture` captures a manual job while `framing`; `/cancel` requests cancellation at any active stage.
- `POST /v1/page-turns/reserve` grants a 15-second reservation. The tracker releases its eye camera before sending serial, then `/v1/page-turns/{id}/commit` starts settling and OCR. Reservations require different eye/book indexes; `camera_index: null` reserves a turn with settling only.

All entry points share one exclusive coordinator, including the compatibility
routes `/v1/scan-camera` and `/v1/auto-scans` (now return job metadata with `202`).
Conflicting requests return `409`; repeated trigger IDs are idempotent. The last
32 jobs retain their own result, and missing IDs after restart return `404`.
Accepted automatic scans matching the previous accepted text report `unchanged`.

Each scan runs in a supervised spawned process. Before starting that process,
the coordinator waits up to ten seconds for the tracker's explicit camera-release
acknowledgement. The worker then waits up to ten seconds to acquire the shared
OS camera lease before opening a camera. Both waits report
`waiting_for_eye_camera` and have a 15-second stage watchdog. Other stage deadlines are: opening
camera 15 seconds, framing 60, capture 15, transcription/revision 55 each, and
preview 8, with an overall 180-second deadline. Provider calls use a 45-second
timeout with automatic retries disabled. Cancellation signals the worker, then
kills it after one second if still running. Status remains `cancelling` until the
worker exits and any physical page-turn guard ends. A cancelled/expired reservation
retains that guard through its lease plus settling, covering an in-flight serial
command. Completed cancelled/timed-out jobs cannot publish late OCR results.

Run **one Uvicorn worker** for this local hardware service: the ownership gate and
settings are in memory. The tracker refuses turns if the API is unavailable.
Developer's **Test 3 blinks** starts a `test` scan without requiring the native
tracker or page-flipper hardware, but still uses the selected OCR camera after
eight seconds and sends no serial command.
The coordinator admits only one scan or page-turn reservation at a time. New
requests while it is busy return HTTP 409 and are skipped immediately, with no
queue or automatic retry. After the worker exits and releases the camera, only
a fresh request can start another scan.

The actual eye reader, OCR worker, and standalone OCR program hold the same OS
file lease at `backend/.runtime/cameras.lock` before opening a camera. On Windows
this uses `msvcrt` locking. The lease remains with the actual camera owner across
an API restart; resetting in-memory job state does not free it. A job's pause
request is cleared after worker exit and the physical guard, then the tracker
reopens its camera with fresh gestures. Never delete or replace a held lock file.
External camera apps and older tracker processes do not participate: close those
apps and restart the tracker after updating. The standalone OCR CLI reports a
busy camera lease instead of requesting an automatic tracker pause.

If the tracker crashes without an explicit release acknowledgement, close its
process and restart FastAPI before manual scanning. Heartbeat expiry alone is
not evidence that the camera was released. With the tracker stopped, the current
standalone book-camera command from the repository root is
`python ".\backend\computer vision\book_ocr.py" --camera 2`.

## Live book-camera framing

Developer's **Live camera** and **Scan OCR** both start a manual job. The worker
waits for any running eye tracker to release its camera, then opens the selected
book camera once and sends live frames to the browser. No tracker is required
for this workflow. **Capture
now** uses that same camera handle to select sharp frames and start OCR. **Cancel
scan** releases the job, and abandoned framing expires after 60 seconds. Web
framing uses no OpenCV window or keyboard input. The standalone `book_ocr.py`
diagnostic still has its native preview and `C`/`Q` controls.

The frame producer emits bounded JPEGs at most five times per second. The
coordinator retains only the newest frame in memory, outside ordinary status and
result payloads. Capture age and a monotonic deadline expire frames after two
seconds; capture, cancellation, and terminal transitions clear them immediately.
The preview endpoint never opens a second camera or calls an OCR provider.

The frontend polls this endpoint at most four times per second while Developer
is visible and the manual job is opening or framing. It clears stale images and
pauses polling when switching to Reader or hiding the browser tab. This only
pauses image polling: the framing job keeps camera ownership until capture,
cancellation, or timeout. Opening the preview does not require OpenAI credentials;
**Capture now** requires the configured key and model to produce an OCR result.

Windows book-camera capture uses DirectShow only. A local camera-2 probe opened
and read frames through DirectShow, while MSMF stalled during opening. This is a
driver-specific observation, not an explanation for every camera timeout. If the
preview cannot open, check the camera index, desktop camera permissions, and
other processes using that camera. Isolated workers and opening deadlines still
bound driver failures.

## Captured page preview

The completed job's `result` includes `capture_preview` after a page is captured, even
when AI rejects its text. It contains a JPEG data URL, its pixel `width` and
`height`, actual detected `words`, and `boxes_status`. A cancelled scan or missing
frame returns null. The preview uses the same perspective-corrected page passed
to AI, resized to at most 1200 pixels on its longest side; no image is saved.

Word geometry comes from one optional local Tesseract pass on that preview,
limited to two seconds and 1000 words at least 50% confident. Each word has `text`,
`x`, `y`, `width`, `height`, and `confidence`; coordinates and confidence are
normalized from 0 to 1. Boxes align with the returned photograph. This pass does
not replace the AI transcription or affect its acceptance decision and makes no
additional external provider calls.

Install the Tesseract executable and its English language data separately to get
word boxes (the Python `pytesseract` dependency alone is insufficient). Without
it, or if the local pass times out, the photo still appears with no boxes and
`boxes_status: "unavailable"`. A successful pass with no confident words reports
`"no_words"`; a nonempty geometry result reports `"available"`. No placeholder
boxes are generated.

## Checks

```powershell
python -m pytest
python -m ruff check .
```

Never commit `.env` or camera diagnostic files.
