<<<<<<< HEAD
from fastapi import FastAPI

from app.api.router import api_router
from app.core.config import settings


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )
    app.include_router(api_router, prefix=settings.api_prefix)
    return app


app = create_app()
=======
from uuid import uuid4

import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from .config import settings


app = FastAPI(title="LOOB Reading Companion API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.allowed_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=2_000)
    page_text: str = Field(min_length=1, max_length=12_000)


class SpeechRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5_000)


def require_openai_key() -> None:
    if not settings.openai_api_key:
        raise HTTPException(503, "OpenAI is not configured. Add OPENAI_API_KEY to backend/.env.")


def require_elevenlabs() -> None:
    if not settings.elevenlabs_api_key:
        raise HTTPException(503, "ElevenLabs is not configured. Add ELEVENLAB_API to backend/.env.")


@app.get("/health")
async def health() -> dict[str, bool]:
    return {"ok": True}


@app.post("/v1/ask")
async def ask(request: Question) -> dict[str, str]:
    require_openai_key()
    client = AsyncOpenAI(api_key=settings.openai_api_key)
    prompt = (
        "You are LOOB, a warm reading companion. Answer the reader's question using "
        "the passage below. Be concise, supportive, and say when the passage does not "
        "provide enough evidence.\n\n"
        f"Passage:\n{request.page_text}\n\nReader question: {request.question}"
    )

    try:
        response = await client.responses.create(model=settings.openai_model, input=prompt)
    except Exception as error:
        raise HTTPException(502, "LOOB could not reach OpenAI. Please try again.") from error

    answer = response.output_text.strip()
    if not answer:
        raise HTTPException(502, "LOOB received an empty answer. Please try again.")

    return {"answer": answer, "request_id": str(uuid4())}


@app.post("/v1/transcribe")
async def transcribe(audio: UploadFile = File(...)) -> dict[str, str]:
    require_elevenlabs()
    if not (audio.content_type or "").startswith("audio/"):
        raise HTTPException(415, "Please record or upload an audio file.")

    contents = await audio.read()
    if len(contents) > 10 * 1024 * 1024:
        raise HTTPException(413, "Audio must be 10 MB or smaller.")
    if not contents:
        raise HTTPException(400, "The audio recording was empty.")

    headers = {"xi-api-key": settings.elevenlabs_api_key}
    data = {"model_id": settings.elevenlabs_stt_model}
    files = {"file": (audio.filename or "voice.webm", contents, audio.content_type)}
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            result = await client.post(
                "https://api.elevenlabs.io/v1/speech-to-text",
                headers=headers,
                data=data,
                files=files,
            )
            result.raise_for_status()
    except httpx.HTTPStatusError as error:
        if error.response.status_code in (401, 403):
            raise HTTPException(502, "ElevenLabs rejected the configured API key.") from error
        if error.response.status_code == 429:
            raise HTTPException(429, "ElevenLabs is busy. Please try again shortly.") from error
        raise HTTPException(502, "ElevenLabs could not transcribe that recording.") from error
    except httpx.HTTPError as error:
        raise HTTPException(502, "LOOB could not reach ElevenLabs.") from error

    payload = result.json()
    text = str(payload.get("text", "")).strip()
    if not text:
        raise HTTPException(422, "LOOB could not hear a spoken question in that recording.")
    return {"text": text, "language": str(payload.get("language_code", ""))}


@app.post("/v1/speech")
async def speech(request: SpeechRequest) -> Response:
    require_elevenlabs()
    if not settings.elevenlabs_voice_id:
        raise HTTPException(503, "Add ELEVENLAB_VOICE_ID to backend/.env to enable speech.")

    headers = {"xi-api-key": settings.elevenlabs_api_key, "accept": "audio/mpeg"}
    body = {
        "text": request.text,
        "model_id": settings.elevenlabs_tts_model,
        "voice_settings": {"stability": 0.55, "similarity_boost": 0.7},
    }
    url = (
        "https://api.elevenlabs.io/v1/text-to-speech/"
        f"{settings.elevenlabs_voice_id}?output_format=mp3_44100_128"
    )
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            result = await client.post(url, headers=headers, json=body)
            result.raise_for_status()
    except httpx.HTTPStatusError as error:
        if error.response.status_code in (401, 403):
            raise HTTPException(502, "ElevenLabs rejected the configured API key or voice ID.") from error
        if error.response.status_code == 429:
            raise HTTPException(429, "ElevenLabs is busy. Please try again shortly.") from error
        raise HTTPException(502, "ElevenLabs could not create the narration.") from error
    except httpx.HTTPError as error:
        raise HTTPException(502, "LOOB could not reach ElevenLabs.") from error

    return Response(content=result.content, media_type="audio/mpeg")
>>>>>>> origin/reading-companion
