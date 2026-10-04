# LOOB Reading Companion

LOOB is a reading companion for book passages. Readers can ask questions by text or voice, hear passages read aloud, and use the camera OCR prototype to capture book pages for reading assistance.

## Before running

Prerequisites:

- Node.js 20 or later
- Python 3.11 or later
- A USB camera for the computer-vision programs
- [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) only when using the legacy `--local-ocr` troubleshooting fallback

## Install all project dependencies

Run this once from the repository root. `backend/requirements.txt` includes the FastAPI, OpenAI, OCR, eye-tracking, test, and lint packages. The Next.js packages remain in `package.json`, so npm installs those separately.

```powershell
py -3.11 -m venv backend\.venv
.\backend\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r backend\requirements.txt
npm install
```

## Run the reading app

Open two PowerShell windows from the repository root.

**1. Start the FastAPI service**

```powershell
.\backend\.venv\Scripts\Activate.ps1
# Create backend\.env and add OPENAI_API_KEY, ELEVENLAB_API, and ELEVENLAB_VOICE_ID.
# Set ALLOWED_ORIGINS=http://localhost:3000,http://127.0.0.1:3000 if needed.
Set-Location backend
python -m uvicorn app.main:app --reload --port 8001
```

**2. Start the Next.js app**

```powershell
npm run dev
```

Open [http://localhost:3000](http://localhost:3000). The frontend expects the API at `http://127.0.0.1:8001` unless `NEXT_PUBLIC_API_BASE_URL` is set.

## Scan a physical page into the reading screen

With the FastAPI service and Next.js app running, use **Scan page** above the story. A local **LOOB OCR calibration** window opens. Select the USB camera index (normally `1`), center the book page inside the yellow guide, then press **C** in that camera window to capture it; press **Q** or Escape to cancel. A successful scan replaces the displayed passage; paragraph playback and LOOB questions then use the scanned text.

The web scanner uses AI vision as its only text extractor. After you press **C** in the calibration window, it sends the selected, perspective-corrected image to OpenAI for transcription. It extracts any legible visible text, including handwritten notes and whiteboards; people and other scenery are ignored rather than causing a rejection. OpenCV is still used only to capture the sharpest frame and correct page perspective; Tesseract text is not used by the reading app.

If a scan is rejected, the Uvicorn terminal logs the configured model plus the AI reason and confidence. This distinguishes a page-quality rejection from an API/model configuration error.

If the first vision transcript reports confidence below 92%, the app makes one additional image-grounded revision request. The revision sees the page image and first transcript, and must reject the scan if it cannot resolve doubtful words from the image. High-confidence first transcripts skip this second request. Set `OPENAI_OCR_MODEL` for transcription and, optionally, `OPENAI_OCR_REVIEW_MODEL` for the final revision; either falls back to `OPENAI_MODEL`.

Close the standalone `book_ocr.py` preview, Windows Camera, Teams, Zoom, and any other program using that camera before scanning from the web app.

## Run the book OCR camera test

Install the Python dependencies once, then start the OCR program from the repository root:

```powershell
.\backend\.venv\Scripts\Activate.ps1
python ".\backend\computer vision\book_ocr.py" --camera 1
```

Use `--camera 0` for the laptop camera or `--camera 1` for the first external camera. The program prints the chosen index before opening it.

- Press `c` to scan the current page.
- Press `f` to trigger the placeholder page-flip action, wait for the page to settle, then scan.
- Press `q` to quit.

Before launching, close Teams, Zoom, Discord, the Windows Camera app, and browser tabs that may be using the webcam. In **Settings → Privacy & security → Camera**, allow camera access for desktop apps. If Windows asks for access when the preview opens, allow it.

Results and diagnostics are written to `camera_ocr_output/`. AI OCR saves the source frame, corrected image, and metrics JSON; the legacy `--local-ocr` mode also saves preprocessing and word-confidence overlays.

### AI vision OCR

The standalone camera tool uses OpenAI vision as its default text extractor. `backend/.env` needs an `OPENAI_API_KEY` plus a vision-capable model in either the existing `OPENAI_MODEL` field or an OCR-specific `OPENAI_OCR_MODEL` field:

```powershell
OPENAI_API_KEY=your_api_key
OPENAI_OCR_MODEL=your_vision_capable_model  # Optional when OPENAI_MODEL is already suitable.
```

Then run:

```powershell
python ".\backend\computer vision\book_ocr.py" --camera 1
```

Use `--openai-revision-model <model>` to override the second-pass model for the standalone camera tool.
Use `--local-ocr` only when you need the legacy offline Tesseract fallback for troubleshooting.

The vision pass receives only the selected, corrected image and returns any legible visible text, including handwriting and whiteboard notes. It ignores people and surrounding scenery rather than rejecting the scan because they are present. The request uses `store=False` to avoid persisted Responses application state. It is not a blanket no-retention guarantee; review [OpenAI's data controls](https://developers.openai.com/api/docs/guides/your-data) and do not scan material you are not authorized to send to OpenAI.

## Run the eye-tracking camera program

After the unified dependency install, run the eye-tracking prototype with:

```powershell
python ".\backend\computer vision\eyetracking_opencv.py" --camera 1
```

The eye tracker requires MediaPipe `0.10.14` because it uses the legacy Face Mesh iris-landmark API. The unified requirements file installs that version. If a newer MediaPipe version was already installed, force the compatible version from `backend/computer vision`:

```powershell
python -m pip install --force-reinstall "mediapipe==0.10.14"
python "eyetracking_opencv.py" --camera 1
```

Use `--camera 0` to try the laptop camera, or add `--port COM3` when the page-flipper hardware is connected.

The eye tracker starts in `STOP` mode. Hold a steady camera gaze for three seconds to enter `READ` mode; look away briefly before another three-second hold can toggle the mode again. In `READ` mode, look down in the configured direction and blink twice to emit the page-flip command. Holding the eyes closed for ten seconds returns the controller to `STOP` as a safety measure.

## Backend checks

Run:

```powershell
Set-Location backend
python -m pytest
python -m ruff check .
```

Keep `.env` files, camera captures, and generated OCR output out of Git.
