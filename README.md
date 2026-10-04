# LOOB Reading Companion

LOOB is a reading companion for book passages. Readers can ask questions by text or voice, hear passages read aloud, and use the camera OCR prototype to capture book pages for reading assistance.

## Before running

Prerequisites:

- Node.js 20 or later
- Python 3.11 or later
- Two separate cameras for eye tracking and book OCR, used one at a time (current setup: eye camera `1`, book camera `2`); one camera is enough for manual OCR or the eye-only prototype
- [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) is optional for word-box overlays on captured pages and the legacy `--local-ocr` troubleshooting fallback

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

## Reader and Developer views

The **Reader** view opens a notebook with an actual question-and-answer journal on the left and the story on the right. The journal starts empty and uses Architects Daughter handwriting. Record a question with the microphone beside **Voice log**, or type and send a question beneath that heading. Use **Read** or **Stop** in the story header to control narration. The story uses 16px text; words explicitly asked about are highlighted yellow, with an actual answer shown on a sticky note. **Mode: Reading** identifies this view, and the footer counts distinct words asked about. The sample suspense story remains available for sound testing. Paper and bookmark artwork and fonts are bundled locally.

Click the green bookmark to switch to the **Developer** view. Question, microphone, and narration controls share their state with Reader; answer replay, immersive sound settings, and scanning controls remain in Developer. The appendix also shows the eye diagram, relative eye openness, gaze direction, blink phase/count, frame timing, OCR results, and real session events. Click the bookmark again to return to the Reader. It also works with Enter or Space when focused. Switching views preserves the page, question draft, sound preferences, and playback. On phones the notebook pages stack vertically.

To use two cameras, run the tracker in another terminal (with the backend environment activated) while FastAPI is already running:

```powershell
python ".\backend\computer vision\eyetracking_opencv.py" --camera 1 --ocr-camera 2
```

Camera `1` tracks eyes and camera `2` faces the book in this example. If your eye camera is `2` and book camera is `1`, use `--camera 2 --ocr-camera 1` and select **Cam 1** for manual OCR. Press **C** in the tracker window while keeping your eyes open to calibrate. Developer automatically picks up its measurements and explains whether real blink detection is ready or paused. The diagram estimates iris direction; it does not yet identify a word or location on the book. OCR displays the captured page photo and available actual word boxes. Live measurements poll every 500 ms, retry every two seconds when the tracker is unavailable, and pause while the browser tab is hidden.

LOOB opens the eye camera first. When a scan needs the book camera, the tracker releases its camera and pauses eye measurements. After the scan worker closes and the scan guard ends, eye tracking resumes with a fresh gesture sequence. The two cameras are never intentionally held open together by the updated application.

Diagnostics travel through a background HTTP sender to FastAPI memory. They no longer depend on replacing `eyes.json` on Windows. Upload failures never block the camera loop; only the latest pending measurements are retained, and data older than two seconds is shown as offline. Restart the tracker after updating its code. To verify real blinks, calibrate, enter READ mode or enable **Blink-only test mode**, then blink three times within two seconds. Developer's **Camera recorded 3 / 3** confirmation comes from the real detector's event log; **Test 3 blinks** is a separate simulation.

## Scan a physical page into the reading screen

With the FastAPI service and Next.js app running, choose the book camera index in **Developer** (normally `2` when camera `1` is the eye tracker). Click **Live camera** or **Scan OCR** to open the same live framing view inside the Book camera section. Position the page inside its guide, then choose **Capture now** to run OCR. **Cancel scan** stops the camera job; framing expires after 60 seconds. Web scanning does not open a separate camera window.

Manual scanning works without starting the eye tracker. If the tracker is running, the scan automatically pauses it and waits for its camera to close before opening the book camera. The tracker resumes after completion, failure, or cancellation finishes.

The live view works without an OpenAI key. Running OCR with **Capture now** requires `OPENAI_API_KEY` and `OPENAI_OCR_MODEL` or `OPENAI_MODEL` in `backend/.env`. Preview and capture use one camera handle, so no second process competes for the camera. A successful scan replaces the displayed passage; narration and LOOB questions then use that text. Developer retains the completed scan photo, result, duration, and available OCR metrics.

The browser refreshes the live image up to four times per second. Images stay in memory and disappear when stale by two seconds, captured, cancelled, or finished. Switching to Reader or hiding the browser tab pauses image polling; the framing job still owns the camera until capture, cancellation, or its 60-second timeout. Choose **Cancel scan** to end the preview without waiting for that timeout.

### Page-turn delay and scan controls

For hands-free capture, start the tracker with `--camera 1 --ocr-camera 2`. Three confirmed blinks within the two-second window obtain a 15-second page-turn reservation. The tracker fully releases the eye camera before sending `flip right`, then commits the turn to start an **eight-second wait**. Time spent reserving the turn and closing the eye camera does not count toward this delay. Developer shows **OCR starts in 8s** and counts down while the page turns. Before starting the OCR worker, the backend waits up to ten seconds for explicit eye-camera release confirmation. The worker then waits up to ten seconds to acquire the OS camera lease before opening camera `2`. Both waits report `waiting_for_eye_camera` and have a 15-second stage watchdog. After the scan worker exits and its guard ends, the eye camera reopens and requires fresh gestures.

| Trigger | Wait before OCR | Capture behavior |
| --- | --- | --- |
| **Live camera** / **Scan OCR** | No page-turn delay; waits for eye-camera release if needed | Opens the live view in Developer; choose **Capture now** |
| Real three-blink page turn | At least 8 seconds after the command is sent | Captures automatically without a calibration window |
| **Test 3 blinks** | 8 seconds after the simulated blink sequence | Captures automatically; sends no page-flipper command |

Use `--ocr-settle-seconds 10` for a longer wait; the supported range is 8–30 seconds. The server enforces the eight-second minimum for page turns and blink tests even if an older tracker requests a shorter delay. Connect the page flipper with `--port COM3` (or its actual port); without a port, UART commands are previewed in the terminal. The two camera indexes must be different.

### STM32 USB-to-TTL page-turn output

After a confirmed page turn, LOOB maps its internal `flip right` action to the exact ASCII UART frame `50\n` (`0x35 0x30 0x0A`). It uses `115200` baud, 8 data bits, no parity, 1 stop bit, and no flow control by default. Run the tracker with the USB-to-TTL adapter's COM port:

```powershell
python ".\backend\computer vision\eyetracking_opencv.py" --camera 1 --ocr-camera 2 --port COM3 --baud 115200
```

Wire the adapter's **TXD** to the selected STM32 USART **RX**, and adapter **GND** to STM32 **GND**. Use 3.3 V UART logic; do not feed a 5 V adapter TX signal into STM32 RX. This is a one-way connection, so leave adapter RX and STM32 TX disconnected unless you later add a separate return protocol. Do not connect VCC unless the adapter is intentionally and safely powering the board.

The STM32 must use the same 115200 8N1 settings and treat LF as the end of the ASCII text `50`. A successful Python write means the USB serial driver accepted the bytes; it does not prove the STM32 or page-turn mechanism completed an action. LOOB logs the sent bytes as `[UART TX ...]` in the tracker terminal. A second serial-terminal app cannot use the same COM port while LOOB owns it.

**Test 3 blinks** can run without the eye tracker or page-flipper hardware. It still opens the selected book camera and runs OCR after the countdown. If eye tracking is running, it uses the same pause-and-resume handoff as a manual scan.

To test real blinks without the gaze requirement, enable **Blink-only test mode** in Developer. It shows whether the tracker has applied the setting; calibrate the eyes with **C** first. Three blinks can then turn a page outside READ mode, and become available again after scanning finishes. Switching the setting clears partial gestures. Turning it off restores the three-second gaze requirement. The setting defaults off after restarting the API. **Test 3 blinks** is a separate simulated test, described above.

**Only one scan or page turn can be active.** During the countdown, capture, transcription, or cancellation, new scan requests are skipped (HTTP `409`), with no queue or automatic retry. The scan and blink-test buttons are disabled while busy. A request that arrives before the UI learns about another scan shows a skipped notice. Finishing OCR never replays skipped requests: a fresh click or three-blink gesture is needed after the current job releases the camera. An unavailable API also blocks physical page turns.

Scan requests return immediately and show progress separately. **Cancel scan** stays available during an active job. It stops the worker before releasing camera ownership; cancellation during a page turn also waits for the settling guard. Camera and OCR deadlines prevent indefinite waits, and disconnected status automatically recovers when the API returns. Run one FastAPI process for this local hardware coordinator; do not use multiple Uvicorn workers.

After updating backend code, restart FastAPI unless it is running with `--reload`. Restart the native eye tracker to load tracker changes. The eight-second minimum is enforced by the updated API even before the tracker is restarted.

The actual camera owners hold a shared OS lock at `backend/.runtime/cameras.lock`. This prevents updated LOOB processes from opening cameras concurrently, including when FastAPI restarts while an old worker is still closing. Never delete this file to try to unlock a camera; the owning process must release it or exit. Close external camera apps and restart older tracker processes, which do not participate in this handoff.

If the tracker crashes without explicitly confirming camera release, close its process, then restart FastAPI before trying a manual scan. An expired heartbeat or an offline eye display does not prove that the camera was released.

After manual capture or the automatic page-turn wait, the web scanner sends the selected, perspective-corrected image to OpenAI for transcription. AI vision remains the authoritative source of reading text, including handwritten notes and whiteboards; people and other scenery are ignored rather than causing a rejection. OpenCV captures the sharpest frame and corrects page perspective. Optional local Tesseract detects word boxes for the photo overlay; its text does not replace the AI transcript, and unavailable boxes are not simulated.

If a scan is rejected, the Uvicorn terminal logs the configured model plus the AI reason and confidence. This distinguishes a page-quality rejection from an API/model configuration error.

If the first vision transcript reports confidence below 92%, the app makes one additional image-grounded revision request. The revision sees the page image and first transcript, and must reject the scan if it cannot resolve doubtful words from the image. High-confidence first transcripts skip this second request. Set `OPENAI_OCR_MODEL` for transcription and, optionally, `OPENAI_OCR_REVIEW_MODEL` for the final revision; either falls back to `OPENAI_MODEL`.

Close the standalone `book_ocr.py` preview, Windows Camera, Teams, Zoom, and any other program using that camera before scanning from the web app.

On Windows, book-camera capture uses DirectShow without falling back to MSMF. A local camera-2 diagnostic captured frames with DirectShow, while MSMF stalled during camera opening. Other opening failures can still come from the selected index, camera access, or another app using the device. Use **Live camera** to verify the view before attempting OCR.

## Immersive narration

In the Developer view, turn on **Immersive narration**, then choose **Read page**. LOOB classifies each paragraph as neutral, warm, or suspenseful, selects a matching ElevenLabs voice preset, and plays a generated ambient sound for warm or suspenseful passages. Literal story events can also trigger short sounds: a creaking door, footsteps, thunder, or a knock. These cues begin with the matching sentence and are limited to two per paragraph. The **Immersive sounds** slider controls ambience and effects together. **Stop reading** stops voice and all immersive sounds; turning immersive mode off returns to plain narration. Story questions and answers keep the regular voice.

Set `ELEVENLAB_VOICE_ID_WARM` and `ELEVENLAB_VOICE_ID_SUSPENSE` in `backend/.env` to hear distinct voices. If either is absent, that mood uses `ELEVENLAB_VOICE_ID` with different delivery settings. Mood and event planning uses the configured OpenAI model, with local literal-event rules as a fallback. Ambient and event sounds are synthesized in the browser, so they do not need another API key or audio files. Paragraphs with event cues are narrated in shorter sentence clips; the next clip is requested while the current one plays, and completed narration is cached until another page is scanned.

## Run the book OCR camera test

Install the Python dependencies once, then start the OCR program from the repository root:

```powershell
.\backend\.venv\Scripts\Activate.ps1
python ".\backend\computer vision\book_ocr.py" --camera 2
```

Use `--camera 0` for the laptop camera or `--camera 1` for the first external camera. The program prints the chosen index before opening it.

- Press `c` to scan the current page.
- Press `f` to trigger the placeholder page-flip action, wait for the page to settle, then scan.
- Press `q` to quit.

This standalone diagnostic has its own 1.5-second placeholder wait. The eight-second countdown described above belongs to the app's coordinated eye-tracker and OCR workflow.

The standalone OCR program uses the same exclusive camera lock but does not ask the tracker to pause. Stop the eye tracker before launching it; if LOOB already owns the camera lease, the standalone program reports that the cameras are busy. Use Developer's **Live camera** for an automatic handoff.

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
python ".\backend\computer vision\book_ocr.py" --camera 2
```

Use `--openai-revision-model <model>` to override the second-pass model for the standalone camera tool.
Use `--local-ocr` only when you need the legacy offline Tesseract fallback for troubleshooting.

The vision pass receives only the selected, corrected image and returns any legible visible text, including handwriting and whiteboard notes. It ignores people and surrounding scenery rather than rejecting the scan because they are present. The request uses `store=False` to avoid persisted Responses application state. It is not a blanket no-retention guarantee; review [OpenAI's data controls](https://developers.openai.com/api/docs/guides/your-data) and do not scan material you are not authorized to send to OpenAI.

## Run the eye-tracking camera program

After the unified dependency install, run the eye-tracking prototype with:

```powershell
python ".\backend\computer vision\eyetracking_opencv.py" --camera 1 --ocr-camera 2
```

The eye tracker requires MediaPipe `0.10.14` because it uses the legacy Face Mesh iris-landmark API. The unified requirements file installs that version. If a newer MediaPipe version was already installed, force the compatible version from `backend/computer vision`:

```powershell
python -m pip install --force-reinstall "mediapipe==0.10.14"
python "eyetracking_opencv.py" --camera 1
```

Use `--camera 0` to try the laptop camera, pass a different `--ocr-camera` to enable automatic OCR, or add `--port COM3` when the page-flipper hardware is connected. Omit `--ocr-camera` to run eye tracking without automatic OCR. FastAPI still coordinates physical turns and settings, so keep it running even when OCR is disabled.

For eye tracking alone, keep FastAPI running and use:

```powershell
python ".\backend\computer vision\eyetracking_opencv.py" --camera 1
```

The eye tracker starts in `STOP` mode. It continuously drains the webcam and analyzes only the latest frame, reducing stale-frame latency. Hold a steady camera gaze for three seconds to enter `READ` mode; look away briefly before another three-second hold can toggle the mode again. Press `C` while facing the camera and keeping your eyes open for one second; it records separate median open-eye baselines for both eyes. The preview shows capture FPS, MediaPipe FPS, frame age, raw eyelid openness, normalized eyelid ratios, and the `OPEN`/`CLOSING`/`CLOSED`/`REOPENING` detector state.

After calibration, the **first deliberate blink starts one fixed 2-second window** in READ mode (or outside READ with Blink-only test mode enabled). Two more blinks request a page turn; later blinks do not extend the deadline. The terminal prints the blink count, reservation outcome, serial output, and scan status. After the brief page signal, LOOB enters `STOP`. In normal mode, look away and then hold your gaze for three seconds to resume. In blink-only mode, fresh blinks become available after the scan/settling guard clears. The detector recognizes full closure and a rapid relative eyelid drop. Holding the eyes closed for ten seconds clears the gesture and returns the controller to `STOP`.

The default requests a 640x480, 30-FPS stream for responsive processing. If partial blinks still show as `OPEN`, try higher sensitivity and optionally save diagnostics:

```powershell
python "eyetracking_opencv.py" --camera 1 --blink-sensitivity high --diagnostics-csv .\blink-diagnostics.csv
```

`low`, `normal` (default), and `high` control how small an eyelid drop may count as a closure. The CSV is local only and contains timing and landmark measurements, not video frames.

## Backend checks

Run:

```powershell
Set-Location backend
python -m pytest
python -m ruff check .
```

The scan coordinator tests cover the full eight-second delay after the page-turn command, skipped busy requests, and fresh starts after completion. Tracker tests cover discarded gestures while busy. `tests/browser/scan-controls.js` checks the countdown, disabled controls, cancellation, and skipped-request recovery using mocked API responses; run it with gstack `browse eval` on localhost, then reload to restore live data. These checks do not operate cameras or confirm a physical page turn.

Keep `.env` files, camera captures, and generated OCR output out of Git.
