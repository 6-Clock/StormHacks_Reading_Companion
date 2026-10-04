from os import getenv

from dotenv import load_dotenv


load_dotenv()


class Settings:
    openai_api_key = getenv("OPENAI_API_KEY", "")
    openai_model = getenv("OPENAI_MODEL", "gpt-5.4-mini")
    openai_ocr_model = getenv("OPENAI_OCR_MODEL", openai_model)
    openai_ocr_review_model = getenv("OPENAI_OCR_REVIEW_MODEL", openai_ocr_model)
    elevenlabs_api_key = getenv("ELEVENLAB_API", "")
    elevenlabs_voice_id = getenv("ELEVENLAB_VOICE_ID", "")
    elevenlabs_stt_model = getenv("ELEVENLAB_STT_MODEL", "scribe_v2")
    elevenlabs_tts_model = getenv("ELEVENLAB_TTS_MODEL", "eleven_multilingual_v2")
    allowed_origins = tuple(
        origin.strip()
        for origin in getenv("ALLOWED_ORIGINS", getenv("ALLOWED_ORIGIN", "http://localhost:3000")).split(",")
        if origin.strip()
    )


settings = Settings()
