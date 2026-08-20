"""Notion delivery.

Called after a pipeline finishes, never from inside one: publishing is
delivery, not authoring. A Notion failure is reported separately and never
blocks Spring from storing the markdown, so every path here returns a
`NotionWriteResult` instead of raising.

Transport and schema live in `notion_mcp` / `notion_schema`. This file only
decides what gets written, under what title, and when to not write at all.
"""

from __future__ import annotations

from datetime import date, timedelta, timezone
from typing import Any

from app.contracts import (
    ArtifactPayload,
    JobError,
    NotionTarget,
    NotionWriteResult,
    utcnow,
)
from app.publish.notion_dashboard import (
    DashboardRef,
    OutsideDashboard,
    ensure_dashboard,
    guard_parent,
    upsert_child,
)
from app.publish.notion_mcp import NotionSession, notion_mcp_url, open_session

KST = timezone(timedelta(hours=9))

__all__ = [
    "DashboardRef",
    "ensure_dashboard_page",
    "kst_today",
    "log_title",
    "notion_mcp_url",
    "publish_artifact",
    "publish_markdown",
    "read_page",
]


def kst_today() -> date:
    """Log dates follow the public API's KST convention, not the server clock."""
    return utcnow().astimezone(KST).date()


def log_title(artifact: ArtifactPayload, target: NotionTarget | None) -> str:
    if target is not None and (target.title or "").strip():
        return target.title.strip()
    when = (target.log_date if target else None) or kst_today()
    return f"{artifact.title} {when.isoformat()}"


async def publish_artifact(
    artifact: ArtifactPayload | None,
    *,
    notion_token: str,
    target: NotionTarget | None,
    session: NotionSession | None = None,
) -> NotionWriteResult:
    if artifact is None:
        return NotionWriteResult(skipped_reason="no_artifact")
    return await publish_markdown(
        title=log_title(artifact, target),
        markdown=artifact.body_markdown,
        notion_token=notion_token,
        parent_id=target.parent_id if target else None,
        session=session,
    )


async def publish_markdown(
    *,
    title: str,
    markdown: str,
    notion_token: str,
    parent_id: str | None = None,
    session: NotionSession | None = None,
) -> NotionWriteResult:
    token = (notion_token or "").strip()
    if not token:
        return NotionWriteResult(skipped_reason="missing_token")
    if not (markdown or "").strip():
        return NotionWriteResult(skipped_reason="no_markdown")
    try:
        live = session if session is not None else await open_session(token)
        dashboard = await guard_parent(live, parent_id)
        page_id, page_url, _ = await upsert_child(
            live, parent_id=dashboard, title=title, markdown=markdown
        )
    except OutsideDashboard as exc:
        # Not a failure of ours to retry: the target is wrong, and writing
        # anyway would scatter pages across the user's workspace.
        return NotionWriteResult(attempted=False, skipped_reason=_scrub(str(exc), token))
    except Exception as exc:
        return _failed(_scrub(str(exc), token) or "notion write failed")
    if not page_id:
        return _failed("notion create page returned no id")
    return NotionWriteResult(attempted=True, ok=True, page_id=page_id, page_url=page_url)


async def ensure_dashboard_page(
    *,
    notion_token: str,
    known_page_id: str | None = None,
    session: NotionSession | None = None,
) -> DashboardRef:
    """Spring calls this once after the user connects Notion, then stores the id."""
    live = session if session is not None else await open_session(notion_token)
    return await ensure_dashboard(live, known_page_id=known_page_id)


async def read_page(
    target: str, *, notion_token: str, session: NotionSession | None = None
) -> Any:
    """Read a page back. Used by live verification, not by the job path."""
    live = session if session is not None else await open_session(notion_token)
    return await live.read_page(target)


def _failed(message: str) -> NotionWriteResult:
    return NotionWriteResult(
        attempted=True,
        ok=False,
        error=JobError(code="internal", message=message, retryable=True),
    )


def _scrub(text: str, token: str) -> str:
    return (text.replace(token, "«token»") if token else text)[:300]
