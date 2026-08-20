"""POST /internal/executions — the only write path into GitHub."""

from __future__ import annotations

import asyncio
import inspect
import os
from collections.abc import Awaitable
from typing import TypeVar

from fastapi import APIRouter

from app.api.deps import GitHubPat, InternalKey
from app.contracts import ErrorCode, ExecuteRequest, ExecuteResult, JobError
from app.execute import execute_readme_pr

router = APIRouter()

DEFAULT_TIMEOUT = "60"

T = TypeVar("T")


async def _maybe_await(value: T | Awaitable[T]) -> T:
    if inspect.isawaitable(value):
        return await value
    return value


@router.post("/internal/executions", response_model=ExecuteResult)
async def post_execution(
    req: ExecuteRequest,
    x_github_pat: GitHubPat = None,
    _: None = InternalKey,
) -> ExecuteResult:
    pat = (x_github_pat or "").strip()
    if not pat:
        return _rejected(req.execution_id, "missing_pat", "GitHub PAT header is required", False)
    try:
        return await asyncio.wait_for(
            _maybe_await(execute_readme_pr(req, pat)),
            timeout=float(os.environ.get("EXECUTION_TIMEOUT", DEFAULT_TIMEOUT)),
        )
    except asyncio.TimeoutError:
        return _rejected(req.execution_id, "internal", "execution timed out", True)
    except Exception:
        return _rejected(req.execution_id, "internal", "execution failed", False)


def _rejected(
    execution_id: str, code: ErrorCode, message: str, retryable: bool
) -> ExecuteResult:
    return ExecuteResult(
        execution_id=execution_id,
        status="rejected",
        error=JobError(code=code, message=message, retryable=retryable),
    )
