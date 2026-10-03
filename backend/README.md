# LOOB backend

This FastAPI service keeps OpenAI and ElevenLabs credentials on the server.

```powershell
cd backend
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn app.main:app --reload --port 8001
```

Update `.env` with valid keys and an ElevenLabs voice ID before trying AI, transcription, or narration. Never commit `.env`.
