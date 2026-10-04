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

Open [http://127.0.0.1:8001/docs](http://127.0.0.1:8001/docs) for the API documentation. Health checks are available at `/health` and `/api/v1/health`.

## Checks

```powershell
python -m pytest
python -m ruff check .
```

Never commit `.env` or camera diagnostic files.
