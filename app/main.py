from __future__ import annotations

from fastapi import FastAPI

from app.api.executions import router as executions_router
from app.api.jobs import router as jobs_router


def create_app() -> FastAPI:
    application = FastAPI(
        title="Blocki-AI",
        version="0.3.0",
        description="Internal GitHub worker. Spring-only. No public browser API. No Notion.",
        docs_url="/docs",
        redoc_url=None,
    )
    application.include_router(jobs_router)
    application.include_router(executions_router)

    @application.get("/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    return application


app = create_app()
