"""Notion delivery.

Called after a pipeline finishes, never from inside one: publishing is
delivery, not authoring. A Notion failure is reported separately and never
blocks Spring from storing the markdown, so every path here returns a
`NotionWriteResult` instead of raising.

Transport and schema live in `notion_mcp` / `notion_schema`. This file only
decides what gets written, under what title, and when to not write at all.
"""

from __future__ import annotations

import asyncio
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
    briefs: list[dict[str, str]] | None = None,
    publish_warnings: list[str] | None = None,
) -> NotionWriteResult:
    if artifact is None:
        return NotionWriteResult(skipped_reason="no_artifact")
    if artifact.kind == "portfolio" and briefs:
        return await _publish_portfolio(
            artifact,
            briefs=briefs,
            notion_token=notion_token,
            target=target,
            session=session,
            publish_warnings=publish_warnings,
        )
    return await publish_markdown(
        title=log_title(artifact, target),
        markdown=artifact.body_markdown,
        notion_token=notion_token,
        parent_id=target.parent_id if target else None,
        session=session,
    )


async def _publish_portfolio(
    artifact: ArtifactPayload,
    *,
    briefs: list[dict[str, str]],
    notion_token: str,
    target: NotionTarget | None,
    session: NotionSession | None,
    publish_warnings: list[str] | None,
) -> NotionWriteResult:
    token = (notion_token or "").strip()
    if not token:
        return NotionWriteResult(skipped_reason="missing_token")
    if not (artifact.body_markdown or "").strip():
        return NotionWriteResult(skipped_reason="no_markdown")
    notes = publish_warnings if publish_warnings is not None else []
    try:
        live = session if session is not None else await open_session(token)
        dashboard = await guard_parent(live, target.parent_id if target else None)
        when = (target.log_date if target else None) or kst_today()
        portfolio_title = log_title(artifact, target)
        hub_title = f"프로젝트 {when.isoformat()}"

        hub_res, port_res = await asyncio.gather(
            upsert_child(live, parent_id=dashboard, title=hub_title, markdown="# 프로젝트\n"),
            upsert_child(
                live,
                parent_id=dashboard,
                title=portfolio_title,
                markdown=artifact.body_markdown,
            ),
            return_exceptions=True,
        )
    except OutsideDashboard as exc:
        return NotionWriteResult(attempted=False, skipped_reason=_scrub(str(exc), token))
    except Exception as exc:
        return _failed(_scrub(str(exc), token) or "notion write failed")
    if isinstance(port_res, BaseException):
        return _failed(_scrub(str(port_res), token) or "notion write failed")
    page_id, page_url, _ = port_res
    if not page_id:
        return _failed("notion create page returned no id")
    if isinstance(hub_res, BaseException) or not hub_res[0]:
        notes.append("프로젝트 허브를 만들지 못했습니다")
        return NotionWriteResult(attempted=True, ok=True, page_id=page_id, page_url=page_url)
    hub_id, _, _ = hub_res

    children = await asyncio.gather(
        *(_upsert_brief(live, hub_id, brief) for brief in briefs),
        return_exceptions=True,
    )
    mentions: list[str] = []
    for brief, item in zip(briefs, children):
        if isinstance(item, BaseException):
            notes.append(f"{brief['title']} 페이지를 쓰지 못했습니다")
            continue
        child_id, child_url = item
        if not child_id:
            notes.append(f"{brief['title']} 페이지를 쓰지 못했습니다")
            continue
        mentions.append(f'<page url="{child_url or f"https://notion.so/{child_id}"}">{brief["title"]}</page>')
    try:
        await live.update_page(hub_id, _hub_index(mentions))
    except Exception:
        notes.append("프로젝트 허브 인덱스를 갱신하지 못했습니다")
    return NotionWriteResult(attempted=True, ok=True, page_id=page_id, page_url=page_url)


async def _upsert_brief(
    session: NotionSession, hub_id: str, brief: dict[str, str]
) -> tuple[str | None, str | None]:
    page_id, page_url, _ = await upsert_child(
        session,
        parent_id=hub_id,
        title=brief["title"],
        markdown=brief["markdown"],
    )
    return page_id, page_url


def _hub_index(mentions: list[str]) -> str:
    if not mentions:
        return "# 프로젝트\n"
    return "# 프로젝트\n\n" + "\n".join(mentions) + "\n"


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
