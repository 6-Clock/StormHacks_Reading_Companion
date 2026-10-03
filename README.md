# LOOB Reading Companion

LOOB is a small reading companion for asking questions about a passage by typing or speaking. It uses OpenAI for helpful answers and ElevenLabs for speech-to-text and narration.

## Project provenance

This repository is a new implementation created from project requirements on October 3, 2026. It replaces an earlier prototype and does not copy that prototype's application source code or assets. Credentials are not included.

## Run locally

Open two PowerShell windows.

**API service**

```powershell
cd C:\Users\artur\Desktop\StormHacks_Reading_Companion_Rebuild\backend
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
# Add your real OPENAI_API_KEY, ELEVENLAB_API, and ELEVENLAB_VOICE_ID to .env
uvicorn app.main:app --reload --port 8001
```

**Reading app**

```powershell
cd C:\Users\artur\Desktop\StormHacks_Reading_Companion_Rebuild
Copy-Item .env.local.example .env.local
npm run dev
```

Open [http://localhost:3000](http://localhost:3000). When prompted, allow microphone access to ask a spoken question.

## Safety notes

- Keep `.env` and `.env.local` out of Git.
- The browser only talks to the local backend; OpenAI and ElevenLabs keys are never exposed to client-side code.
- If port 3000 or 8001 is already in use, choose another port and update `NEXT_PUBLIC_LOOB_API_URL` or `ALLOWED_ORIGIN` to match.
