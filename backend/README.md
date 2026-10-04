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

## Checks

```powershell
python -m pytest
python -m ruff check .
```

Never commit `.env` or camera diagnostic files.
