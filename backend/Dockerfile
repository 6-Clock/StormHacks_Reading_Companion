FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /bin/uv

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY app ./app

RUN uv sync --frozen --no-dev --no-editable --no-cache --python /usr/local/bin/python
RUN .venv/bin/python -c "import cv2; from app.main import app"

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
