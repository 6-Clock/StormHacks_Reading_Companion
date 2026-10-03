# LOOB Reading Companion

LOOB is a reading companion for book passages. Readers can ask questions by text or voice, hear passages read aloud, and use the camera OCR prototype to capture book pages for reading assistance.

## Before running

This checkout currently contains unresolved Git conflict markers in `backend/app/main.py` and `backend/README.md`. Resolve the `<<<<<<<`, `=======`, and `>>>>>>>` sections in those files before starting the web app. The OCR camera program can be run independently.

Prerequisites:

- Node.js 20 or later
- Python 3.11 or later
- A USB camera for the computer-vision programs
- [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) installed and available on `PATH` for the book OCR program

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
Set-Location backend
python -m uvicorn app.main:app --reload --port 8001
```

**2. Start the Next.js app**

```powershell
npm run dev
```

Open [http://localhost:3000](http://localhost:3000). The frontend expects the API at `http://127.0.0.1:8001` unless `NEXT_PUBLIC_API_BASE_URL` is set.

## Run the book OCR camera test

Install the Python dependencies once, then start the OCR program from the repository root:

```powershell
.\backend\.venv\Scripts\Activate.ps1
tesseract --version
python ".\backend\computer vision\book_ocr.py" --camera 1
```

Use `--camera 0` for the laptop camera or `--camera 1` for the first external camera. The program prints the chosen index before opening it.

- Press `c` to scan the current page.
- Press `f` to trigger the placeholder page-flip action, wait for the page to settle, then scan.
- Press `q` to quit.

Before launching, close Teams, Zoom, Discord, the Windows Camera app, and browser tabs that may be using the webcam. In **Settings → Privacy & security → Camera**, allow camera access for desktop apps. If Windows asks for access when the preview opens, allow it.

Results and diagnostics are written to `camera_ocr_output/`. A rejected scan still saves its source frame, corrected page, preprocessing image, word-confidence overlay, and metrics JSON so its failure can be investigated.

### Optional OpenAI vision review

The normal OCR pipeline stays local. To request a final OpenAI vision transcription of the selected page, `backend/.env` needs an `OPENAI_API_KEY` plus a vision-capable model in either the existing `OPENAI_MODEL` field or an OCR-specific `OPENAI_OCR_MODEL` field:

```powershell
OPENAI_API_KEY=your_api_key
OPENAI_OCR_MODEL=your_vision_capable_model  # Optional when OPENAI_MODEL is already suitable.
```

Then run:

```powershell
python ".\backend\computer vision\book_ocr.py" --camera 1 --openai-review
```

The vision pass receives only the selected, corrected page image and the local OCR draft. It is instructed to return text only for a readable printed book page and to reject whiteboards, faces, handwriting, illustrations, and decorative patterns. The request uses `store=False` to avoid persisted Responses application state. It is not a blanket no-retention guarantee; review [OpenAI's data controls](https://developers.openai.com/api/docs/guides/your-data) and do not enable this option for pages you are not authorized to send to OpenAI.

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

After resolving the merge conflicts, run:

```powershell
Set-Location backend
python -m pytest
python -m ruff check .
```

Keep `.env` files, camera captures, and generated OCR output out of Git.
