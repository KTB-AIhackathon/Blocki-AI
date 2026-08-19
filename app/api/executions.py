from __future__ import annotations

import asyncio
import inspect
import os
import secrets
from collections.abc import Awaitable
from typing import Annotated, TypeVar

from fastapi import APIRouter, Depends, Header, HTTPException

from app.contracts import (
    ExecuteRequest,
    ExecuteResult,
    GITHUB_PAT_HEADER,
    INTERNAL_KEY_HEADER,
    JobError,
)
from app.execute.readme_pr import execute_readme_pr

router = APIRouter()

T = TypeVar("T")


async def _maybe_await(value: T | Awaitable[T]) -> T:
    if inspect.isawaitable(value):
        return await value
    return value


def _require_internal_key(
    x_internal_key: Annotated[str | None, Header(alias=INTERNAL_KEY_HEADER)] = None,
) -> None:
    expected = os.environ.get("INTERNAL_API_KEY")
    if not expected and os.environ.get("PYTEST_CURRENT_TEST"):
        expected = "dev-internal-key"
    if not expected:
        raise HTTPException(status_code=503, detail="INTERNAL_API_KEY not set")
    if x_internal_key is None or not secrets.compare_digest(x_internal_key, expected):
        raise HTTPException(status_code=401, detail="unauthorized")


@router.post("/internal/executions", response_model=ExecuteResult)
async def post_execution(
    req: ExecuteRequest,
    x_github_pat: Annotated[str | None, Header(alias=GITHUB_PAT_HEADER)] = None,
    _: None = Depends(_require_internal_key),
) -> ExecuteResult:
    pat = (x_github_pat or "").strip()
    if not pat:
        return ExecuteResult(
            execution_id=req.execution_id,
            status="rejected",
            error=JobError(
                code="missing_pat",
                message="GitHub PAT header is required",
                retryable=False,
            ),
        )
    try:
        return await asyncio.wait_for(_maybe_await(execute_readme_pr(req, pat)), timeout=60)
    except asyncio.TimeoutError:
        return ExecuteResult(
            execution_id=req.execution_id,
            status="rejected",
            error=JobError(code="internal", message="execution timed out", retryable=True),
        )
    except Exception:
        return ExecuteResult(
            execution_id=req.execution_id,
            status="rejected",
            error=JobError(code="internal", message="execution failed", retryable=False),
        )
