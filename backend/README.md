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
python ".\backend\computer vision\eyetracking_opencv.py" --camera 0
```

Use a different camera index for the book scanner when both are active. The eye
tracker keeps its native preview and controls: `C` calibrates open eyes, `R` resets,
and `Q` or Escape stops it. Three confirmed blinks within two seconds in READ mode
still send `flip right`; developer diagnostics do not change gesture behavior.

`GET /v1/diagnostics/eyes` exposes the latest measurements through a local snapshot
at `backend/.runtime/eyes.json`. The tracker atomically replaces this file at most
five times per second. It contains no images and is excluded from Git. Browsers
can poll every 500 ms; no WebSocket or separate service is required.

The response contains `connected`, `updated_at`, `camera_index`, `mode`,
`eyes_visible`, `gaze`, `openness`, `phase`, `blink_count`, `look_progress`,
`capture_fps`, `inference_fps`, `frame_age_ms`, and `events`. Timestamps use Unix
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

## Captured page preview

`POST /v1/scan-camera` includes `capture_preview` after a page is captured, even
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
