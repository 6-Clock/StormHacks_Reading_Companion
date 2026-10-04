# LOOB Reading Companion

LOOB uses one browser GPT-Live voice session for spoken conversation, typed questions,
and reading pages aloud. GPT-Live delegates reasoning and page-image transcription to
the hosted Responses backend. The default models are `gpt-live-1` and `gpt-6.1-sol`,
with low reasoning effort and Fast processing for the hosted backend.
FastAPI keeps the OpenAI key on the server and coordinates local camera capture.

## Setup

Use Node.js 20.9 or later through nvm-windows, Python 3.11 or 3.12 through uv, and
an OpenAI project with access to GPT-Live and the configured reasoning model.

From the repository root:

```powershell
npm ci
uv sync --project backend --python 3.12
```

`backend/pyproject.toml` and `backend/uv.lock` are the Python dependency source.
Camera capture includes OpenCV; Tesseract and ElevenLabs are no longer used.
Install optional eye-tracking and serial packages with:

```powershell
uv sync --project backend --python 3.12 --extra hardware
```

Create `backend/.env`:

```dotenv
OPENAI_API_KEY=your-key
OPENAI_MODEL=gpt-6.1-sol
OPENAI_REASONING_EFFORT=low
OPENAI_SERVICE_TIER=priority
OPENAI_VOICE=marin
ALLOWED_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
```

Only `OPENAI_API_KEY` is required; the other values above are defaults or optional
configuration. Never put the key in a `NEXT_PUBLIC_` variable.

## Run

Start FastAPI in one terminal:

```powershell
Set-Location backend
uv run uvicorn app.main:app --reload --port 8001
```

Start Next.js from the repository root in another:

```powershell
npm run dev
```

Open [localhost:3000](http://localhost:3000). The API defaults to
`http://127.0.0.1:8001`; use `NEXT_PUBLIC_API_BASE_URL` to change it.
Run one Uvicorn worker: camera and tracker state are local and held in memory.

## Reading and scanning

Start a conversation and allow microphone access. Spoken and typed questions use
the same session; the journal displays its transcript. Read requests narration of
the accepted page. Stop ends playback and the session; reconnect to continue.
Reader and Developer views share the current page and conversation. Accepting a
new page updates that session's context without disconnecting it.

Developer's manual camera workflow opens a preview, then captures the selected
page. Test and automatic captures start immediately after the request. There is
no eight-second settling minimum, physical-turn reservation, confidence-based OCR
revision, or duplicate-text suppression. Capturing does not require provider
credentials; transcription needs a connected browser session. A capture made
while disconnected waits for that session, and the previous page remains visible
until new text is accepted.

The delegated model receives the image and can request another capture of the
same page if it is blurred or moving. That capture tool does not repeat a physical
page turn. If reading still fails, LOOB asks the reader to reposition the book.
There are no word highlights, mood plans, immersive modes, or sound effects.

## Optional eye tracker and page flipper

With FastAPI running, launch the tracker from the repository root:

```powershell
uv run --project backend --extra hardware python ".\backend\computer vision\eyetracking_opencv.py" --camera 1 --ocr-camera 2
```

The example uses eye camera `1` and book camera `2`; the same camera index can
also be reused after the tracker releases it. Omit `--ocr-camera` for eye tracking
without automatic page capture. Press `C` to calibrate open eyes, `R` to reset,
and `Q` or Escape to quit. After calibration, three deliberate blinks within two seconds request a page turn. No gaze hold is required, including after a page turn.

Add `--port COM3 --baud 115200` for the USB-to-TTL adapter. A `flip right` action
sends `50 <request_id>\n` at 115200 8N1 and waits for matching MCU `ACK` and `DONE`
replies before requesting page capture. Connect adapter TXD to STM32 PB7/RX,
adapter RXD to STM32 PA9/TX, and common GND using compatible 3.3 V logic levels.
Without a port the tracker previews the command. Update the MCU firmware together
with the bridge; older firmware does not acknowledge these commands.
`DONE` means the sequence finished using its existing calibrated delays;
it does not measure servo position or prove that a page turned. Rejections,
motor faults, disconnections, and timeouts skip capture without retrying motion.
The completion timeout defaults to 10 seconds; use `--serial-completion-timeout`
to increase it if you lengthen the MCU's command delays.
See [the MCU protocol](autobook_embedded/BOOK_UART_SEQUENCE_README.txt) for timing
and reply details. Camera access still coordinates actual release and cleanup.

The standalone capture diagnostic makes no model calls:

```powershell
uv run --project backend python ".\backend\computer vision\book_ocr.py" --camera 2 --output backend/.runtime/page.jpg
```

## Checks and practical limits

```powershell
uv run --project backend pytest
npm run lint
npm run build
```

Turbopack stalled in the restricted validation environment; the documented
webpack CLI fallback started successfully there. If the same issue occurs locally:

```powershell
npm run dev -- --webpack
npm run build -- --webpack
```

The event-parser unit tests use Node.js 24 or later to import TypeScript directly:

```powershell
node --test tests/unit/live-events.test.mjs
```

The browser tests use mocked camera, microphone, and provider responses. With the
frontend running, install temporary browser tooling and choose an installed
Chrome or Edge executable:

```powershell
npm install --prefix backend/.runtime/node-tools --no-save --package-lock=false playwright-core
$env:PLAYWRIGHT_MODULE_PATH = (Resolve-Path backend/.runtime/node-tools/node_modules/playwright-core/index.mjs).Path
$env:BROWSER_EXECUTABLE_PATH = 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
$env:BROWSER_TEST_URL = 'http://127.0.0.1:3000'
node tests/browser/run.mjs
```

Adjust the browser executable path to your installation. The temporary tooling
does not change the application's dependencies.

See [backend/README.md](backend/README.md) for endpoint details. Automated tests
mock provider responses and camera activity. Live account access, verbatim spoken
narration, interruption behavior, OCR accuracy and latency, and physical camera/
page-flipper operation need validation on the actual setup. A prompt asks the
voice model to narrate verbatim; it does not establish a fidelity guarantee or
word-level playback timing.

Keep `.env` files and generated captures out of Git.
