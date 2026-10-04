from os import getenv

from dotenv import load_dotenv

load_dotenv()


class Settings:
    openai_api_key = getenv("OPENAI_API_KEY", "")
    openai_model = getenv("OPENAI_MODEL", "gpt-6.1-sol")
    openai_reasoning_effort = getenv("OPENAI_REASONING_EFFORT", "low")
    openai_service_tier = getenv("OPENAI_SERVICE_TIER", "priority")
    openai_voice = getenv("OPENAI_VOICE", "marin")
    allowed_origins = tuple(
        origin.strip()
        for origin in getenv("ALLOWED_ORIGINS", getenv("ALLOWED_ORIGIN", "http://localhost:3000")).split(",")
        if origin.strip()
    )


settings = Settings()
