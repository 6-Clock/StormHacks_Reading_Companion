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

Create `backend/.env` with at least `OPENAI_API_KEY`, `OPENAI_MODEL`, `ELEVENLAB_API`, and `ELEVENLAB_VOICE_ID`. Optional OCR-specific models are `OPENAI_OCR_MODEL` and `OPENAI_OCR_REVIEW_MODEL`.

For immersive narration, optionally set `ELEVENLAB_VOICE_ID_WARM` and `ELEVENLAB_VOICE_ID_SUSPENSE` to distinct ElevenLabs voices. `POST /v1/narration-plan` returns validated paragraph moods, sentence boundaries, and up to two literal-event sound cues per paragraph. Approved cues are `door_creak`, `footsteps`, `thunder`, and `knock`. `POST /v1/speech` accepts an optional `mood` (`neutral`, `warm`, or `suspense`) and uses the matching configured voice; missing optional voices fall back to `ELEVENLAB_VOICE_ID`.

Open [http://127.0.0.1:8001/docs](http://127.0.0.1:8001/docs) for the API documentation. Health checks are available at `/health` and `/api/v1/health`.

## Live developer diagnostics

Start the eye tracker separately from the repository root, while FastAPI is running:

```powershell
python ".\backend\computer vision\eyetracking_opencv.py" --camera 1 --ocr-camera 2
```

Camera `1` tracks eyes and camera `2` scans the book. The eye tracker keeps its
native preview and controls: `C` calibrates open eyes, `R` resets, and `Q` or Escape
stops it. Calibrate first. Three confirmed blinks within two seconds reserve the
page-turn gate before sending `flip right`, then commit an OCR job. The job waits
8 seconds for the physical page to settle before capturing without a calibration
window. After the brief signal, the tracker enters `STOP`. Normally, look away and
hold your gaze for three seconds to resume. Developer's **Blink-only test mode**
bypasses that gaze requirement; new blinks remain blocked until scanning/settling
ends. Changing the setting clears partial gestures. Change the wait with
`--ocr-settle-seconds` (8–30 seconds); omit `--ocr-camera` to disable automatic OCR. FastAPI is
still required for coordinated turns without OCR. Camera indexes must differ.

`GET/PATCH /v1/tracker-settings` reads/changes the desired `blink_only` boolean.
The tracker polls in a background thread, applies it in the camera loop, and
posts `/v1/tracker-settings/ack` with the revision and its session ID. The UI
reports applied only while that acknowledgement is fresh (three seconds). The
default is off on API restart; lost API connectivity blocks physical commands.

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
- `POST /v1/scan-jobs/{id}/capture` captures a manual job while `framing`; `/cancel` requests cancellation at any active stage.
- `POST /v1/page-turns/reserve` grants a five-second reservation before the tracker sends serial. `/v1/page-turns/{id}/commit` starts settling and OCR. Reservations require different eye/book indexes; `camera_index: null` reserves a turn with settling only.

All entry points share one exclusive coordinator, including the compatibility
routes `/v1/scan-camera` and `/v1/auto-scans` (now return job metadata with `202`).
Conflicting requests return `409`; repeated trigger IDs are idempotent. The last
32 jobs retain their own result, and missing IDs after restart return `404`.
Accepted automatic scans matching the previous accepted text report `unchanged`.

Each scan runs in a supervised spawned process. Stage deadlines are: opening
camera 15 seconds, framing 60, capture 15, transcription/revision 55 each, and
preview 8, with an overall 180-second deadline. Provider calls use a 45-second
timeout with automatic retries disabled. Cancellation signals the worker, then
kills it after one second if still running. Status remains `cancelling` until the
worker exits and any physical page-turn guard ends. A cancelled/expired reservation
retains that guard through its lease plus settling, covering an in-flight serial
command. Completed cancelled/timed-out jobs cannot publish late OCR results.

Run **one Uvicorn worker** for this local hardware service: the ownership gate and
settings are in memory. The tracker refuses turns if the API is unavailable.
Developer's **Test 3 blinks** starts a `test` scan but sends no serial command.
The coordinator admits only one scan or page-turn reservation at a time. New
requests while it is busy return HTTP 409 and are skipped immediately, with no
queue or automatic retry. After the worker exits and releases the camera, only
a fresh request can start another scan.

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
