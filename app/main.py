from __future__ import annotations

from fastapi import FastAPI

from app.api.executions import router as executions_router
from app.api.jobs import router as jobs_router
from app.api.notion import router as notion_router
from app.log import configure as configure_logging
from app.startup import is_production, verify_environment


def create_app() -> FastAPI:
    configure_logging()
    verify_environment()
    application = FastAPI(
        title="Blocki-AI",
        version="0.4.0",
        description="Internal worker: GitHub 수집 → 분석 → 문서 파이프라인 → Notion 적재.",
        # The schema names every internal route and its payload. Useful locally,
        # an invitation once the port is reachable.
        docs_url=None if is_production() else "/docs",
        openapi_url=None if is_production() else "/openapi.json",
        redoc_url=None,
    )
    application.include_router(jobs_router)
    application.include_router(executions_router)
    application.include_router(notion_router)

    @application.get("/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    return application


app = create_app()
