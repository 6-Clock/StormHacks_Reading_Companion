# StormHacks 2026 Backend

StormHacks 2026을 위한 FastAPI 백엔드 골격입니다. 간단한 상태 확인 API, 환경 설정, 테스트, Docker 실행 구성을 포함합니다.

## 로컬 실행

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env
uvicorn app.main:app --reload
```

- API 문서: http://127.0.0.1:8000/docs
- 상태 확인: http://127.0.0.1:8000/api/v1/health

## 테스트 및 코드 검사

```powershell
pytest
ruff check .
```

## Docker 실행

```powershell
docker build -t stormhacks2026-backend .
docker run --rm -p 8000:8000 stormhacks2026-backend
```
