"""POST /internal/executions — the only write path into GitHub."""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
from collections.abc import Awaitable
from typing import TypeVar

from fastapi import APIRouter

from app.api.deps import GitHubPat, InternalKey
from app.contracts import ErrorCode, ExecuteRequest, ExecuteResult, JobError
from app.execute import execute_readme_pr
from app.log import redact, redact_exc, utc_ts

logger = logging.getLogger(__name__)
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
        return _rejected(req, "missing_pat", "GitHub PAT header is required", False)
    try:
        result = await asyncio.wait_for(
            _maybe_await(execute_readme_pr(req, pat)),
            timeout=float(os.environ.get("EXECUTION_TIMEOUT", DEFAULT_TIMEOUT)),
        )
    except asyncio.TimeoutError as exc:
        return _rejected(req, "internal", "execution timed out", True, exc=exc, secret=pat)
    except Exception as exc:
        return _rejected(req, "internal", "execution failed", False, exc=exc, secret=pat)
    if result.error:
        logger.error(
            "%s",
            redact(
                f"uuid={req.execution_id} ts={utc_ts()} execution_id={req.execution_id} "
                f"status={result.status} error={result.error.code} {result.error.message}",
                pat,
            ),
        )
    return result


def _rejected(
    req: ExecuteRequest,
    code: ErrorCode,
    message: str,
    retryable: bool,
    *,
    exc: BaseException | None = None,
    secret: str = "",
) -> ExecuteResult:
    logger.error(
        "%s",
        redact(
            f"uuid={req.execution_id} ts={utc_ts()} execution_id={req.execution_id} "
            f"status=rejected error={code} {message}",
            secret,
        ),
    )
    if exc is not None:
        logger.error("%s", redact_exc(exc, secret))
    return ExecuteResult(
        execution_id=req.execution_id,
        status="rejected",
        error=JobError(code=code, message=message, retryable=retryable),
    )
