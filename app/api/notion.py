"""POST /internal/notion/dashboard — find or build the user's TIL dashboard.

Spring calls this once, right after the Notion OAuth callback, and stores the
`page_id` it gets back. Every later job sends that id as `notion.parent_id`,
which is the only place this worker will write.

The id is Spring's to keep because this worker holds no state. Keeping it out
of the OAuth `workspace_id` column matters: a workspace is not a page, and
writing to the former is what put pages in the wrong place before.
"""

from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter

from app.api.deps import InternalKey, NotionToken
from app.contracts import ErrorCode, JobError, NotionEnsureRequest, NotionEnsureResult
from app.publish import ensure_dashboard_page

router = APIRouter()

DEFAULT_TIMEOUT = "60"


@router.post("/internal/notion/dashboard", response_model=NotionEnsureResult)
async def post_dashboard(
    req: NotionEnsureRequest,
    x_notion_token: NotionToken = None,
    _: None = InternalKey,
) -> NotionEnsureResult:
    token = (x_notion_token or "").strip()
    if not token:
        return _failed("validation", "Notion token header is required", False)
    try:
        ref = await asyncio.wait_for(
            ensure_dashboard_page(notion_token=token, known_page_id=req.known_page_id),
            timeout=float(os.environ.get("NOTION_ENSURE_TIMEOUT", DEFAULT_TIMEOUT)),
        )
    except asyncio.TimeoutError:
        return _failed("internal", "notion ensure timed out", True)
    except Exception as exc:
        return _failed("internal", _scrub(_exception_message(exc), token), True)
    return NotionEnsureResult(
        ok=True, page_id=ref.page_id, page_url=ref.page_url, created=ref.created
    )


def _failed(code: ErrorCode, message: str, retryable: bool) -> NotionEnsureResult:
    return NotionEnsureResult(
        ok=False, error=JobError(code=code, message=message, retryable=retryable)
    )


def _exception_message(exc: BaseException) -> str:
    parts: list[str] = []

    def walk(error: BaseException) -> None:
        group = getattr(error, "exceptions", None)
        if group:
            for inner in group:
                walk(inner)
            return
        text = str(error).strip()
        if text:
            parts.append(text)

    walk(exc)
    return "; ".join(parts) or type(exc).__name__


def _scrub(text: str, token: str) -> str:
    return ((text.replace(token, "«token»") if token else text)[:300]) or "notion ensure failed"
