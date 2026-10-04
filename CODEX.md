# StormHacks Reading Companion

## Project summary

LOOB is a local reading companion. It displays scanned text, answers reader questions, accepts spoken questions, and narrates passages or answers.

## Architecture

- `src/` — Next.js 16 / React 19 interface.
- `src/components/ReadingDesk.tsx` — reading view, camera scan controls, questions, microphone recording, and playback.
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

Provider keys stay in `backend/.env`; never commit that file.

## AI OCR workflow

1. The reader clicks **Scan page** and selects a camera index.
2. The backend opens **LOOB OCR calibration**. Center the target inside the yellow guide, press `C` to capture, or `Q`/Escape to cancel.
3. OpenCV selects a sharp frame and perspective-corrects it. It does not extract text for the integrated scanner.
4. OpenAI vision transcribes any legible visible text, including book text, handwriting, whiteboards, signs, notes, and partial text. People and scenery are ignored.
5. When first-pass confidence is below 92%, a second image-grounded AI revision verifies the transcript. A clear first pass skips this extra call.
6. Accepted text replaces the reading page; questions and narration use the new text.

The Uvicorn terminal logs each AI OCR model, reason, acceptance result, and confidence. Close any other app holding the selected camera before scanning.

## Configuration

Required for the reading app:

```env
OPENAI_API_KEY=...
OPENAI_MODEL=...
ELEVENLAB_API=...
ELEVENLAB_VOICE_ID=...
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

## Verification

- Next.js production build passes.
- Modified Python modules pass syntax checks.
- Live camera and provider calls require the local hardware, valid keys, and a vision-capable model.

## Pre-push checklist

- Do not commit `.env`, `.venv`, `node_modules`, or camera diagnostics.
- Confirm `git diff --check` is clean.
- Run `python -m pytest` and `python -m ruff check .` from `backend` when the virtual environment is available.
- Review `git status` before staging files.
