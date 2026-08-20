"""Notion delivery.

Called after a pipeline finishes, never from inside one: publishing is
delivery, not authoring. A Notion failure is reported separately and never
blocks Spring from storing the markdown, so every path here returns a
`NotionWriteResult` instead of raising.

Transport and schema live in `notion_mcp` / `notion_schema`. This file only
decides what gets written, under what title, and when to not write at all.

Everything generated lands under the dashboard's `생성된 포트폴리오 및 이력서`
page, not the dashboard itself, so the sidebar keeps one entry instead of one
per run.
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
    child_titles,
    ensure_archive,
    ensure_dashboard,
    guard_parent,
    next_version,
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
    "version_base",
    "with_version",
]


def kst_today() -> date:
    """Log dates follow the public API's KST convention, not the server clock."""
    return utcnow().astimezone(KST).date()


def log_title(artifact: ArtifactPayload, target: NotionTarget | None) -> str:
    if target is not None and (target.title or "").strip():
        return target.title.strip()
    when = (target.log_date if target else None) or kst_today()
    return f"{artifact.title} {when.isoformat()}"


def version_base(artifact: ArtifactPayload, target: NotionTarget | None) -> str:
    """버전을 끼워 넣을 자리. Spring 이 제목을 직접 정했으면 손대지 않는다."""
    if target is not None and (target.title or "").strip():
        return ""
    return artifact.title


def with_version(title: str, base: str, siblings: list[str]) -> str:
    """`이름 이력서 2026-08-20` → `이름 이력서 v3 2026-08-20`.

    웹 아카이브가 판마다 한 줄씩 남기므로 Notion 도 같은 모양이어야 한다.
    날짜만으로는 하루에 아홉 번 돌려도 페이지 하나가 계속 덮어써진다.
    """
    if not base:
        return title
    return title.replace(base, f"{base} v{next_version(siblings, base)}", 1)


async def publish_artifact(
    artifact: ArtifactPayload | None,
    *,
    notion_token: str,
    target: NotionTarget | None,
    session: NotionSession | None = None,
    briefs: list[dict[str, str]] | None = None,
    publish_warnings: list[str] | None = None,
    hub_tail: str | None = None,
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
            hub_tail=hub_tail or "",
        )
    return await publish_markdown(
        title=log_title(artifact, target),
        markdown=artifact.body_markdown,
        notion_token=notion_token,
        parent_id=target.parent_id if target else None,
        session=session,
        version_base=version_base(artifact, target),
    )


async def _publish_portfolio(
    artifact: ArtifactPayload,
    *,
    briefs: list[dict[str, str]],
    notion_token: str,
    target: NotionTarget | None,
    session: NotionSession | None,
    publish_warnings: list[str] | None,
    hub_tail: str = "",
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
        archive = await ensure_archive(live, dashboard)
        when = (target.log_date if target else None) or kst_today()
        # 한 번만 읽어서 문서와 허브 번호를 함께 매긴다.
        siblings = await child_titles(live, archive)
        portfolio_title = with_version(
            log_title(artifact, target), version_base(artifact, target), siblings
        )
        hub_title = with_version(f"프로젝트 {when.isoformat()}", "프로젝트", siblings)

        hub_res, port_res = await asyncio.gather(
            upsert_child(live, parent_id=archive, title=hub_title, markdown="# 프로젝트\n"),
            upsert_child(
                live,
                parent_id=archive,
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
        await live.update_page(hub_id, _hub_index(mentions, hub_tail))
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


def _hub_index(mentions: list[str], tail: str = "") -> str:
    parts = ["# 프로젝트"]
    if mentions:
        parts.append("")
        parts.extend(mentions)
    extra = (tail or "").strip()
    if extra:
        parts.extend(["", extra])
    return "\n".join(parts) + "\n"


async def publish_markdown(
    *,
    title: str,
    markdown: str,
    notion_token: str,
    parent_id: str | None = None,
    session: NotionSession | None = None,
    version_base: str = "",
) -> NotionWriteResult:
    token = (notion_token or "").strip()
    if not token:
        return NotionWriteResult(skipped_reason="missing_token")
    if not (markdown or "").strip():
        return NotionWriteResult(skipped_reason="no_markdown")
    try:
        live = session if session is not None else await open_session(token)
        dashboard = await guard_parent(live, parent_id)
        archive = await ensure_archive(live, dashboard)
        final = with_version(title, version_base, await child_titles(live, archive))
        page_id, page_url, _ = await upsert_child(
            live, parent_id=archive, title=final, markdown=markdown
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
