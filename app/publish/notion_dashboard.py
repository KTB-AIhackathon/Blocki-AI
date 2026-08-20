"""Find, build and guard the one page this agent is allowed to write into.

The rule from `Docs/노션템플릿.md` §2: a single `Developer TIL Dashboard`
directly under the user's private root, and nothing outside it is ours. A
user's workspace is not scratch space, so every write here proves it is landing
inside that page before it happens.

Finding the dashboard lists the private root only. Finding a child reads that
dashboard. Workspace-wide search is not used: it walks pages we do not own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.publish.notion_mcp import NotionSession
from app.publish.notion_schema import children_from, title_of
from app.publish.notion_template import (
    ARCHIVE_PAGE,
    CHILD_PAGES,
    DASHBOARD_ICON,
    DASHBOARD_TITLE,
    is_dashboard_title,
    render_dashboard_body,
)

__all__ = [
    "DashboardRef",
    "OutsideDashboard",
    "child_titles",
    "ensure_archive",
    "ensure_dashboard",
    "find_child",
    "guard_parent",
    "next_version",
    "upsert_child",
]

#: `이름 종류 v3 2026-08-20` 의 버전 자리.
_VERSION = re.compile(r"^\s+v(\d+)\b")


class OutsideDashboard(RuntimeError):
    """A write was aimed at something that is not the dashboard."""


@dataclass(frozen=True)
class DashboardRef:
    page_id: str
    page_url: str | None = None
    created: bool = False


async def ensure_dashboard(
    session: NotionSession, *, known_page_id: str | None = None
) -> DashboardRef:
    """The dashboard's id, reusing it, finding it, or building it — in that order.

    Reuse first because it is one fetch and needs no listing entitlement. List
    the private root second so a user who already has the page never gets a
    second one. Creation only when both come up empty, which is also the path
    for a user who deleted it: §2 rule 4 says rebuild at the root rather than
    adopt another page. Missing children are created from the markdown files
    without overwriting pages the human already has.
    """
    if known_page_id and await _is_dashboard(session, known_page_id):
        await _ensure_children(session, known_page_id)
        return DashboardRef(page_id=known_page_id)

    found = await _find_at_private_root(session)
    if found is not None:
        await _ensure_children(session, found.page_id)
        return found

    return await _build(session)


async def guard_parent(session: NotionSession, parent_id: str | None) -> str:
    """Return `parent_id` once confirmed to be the dashboard, else raise.

    Raises `OutsideDashboard` for a missing, unreadable or wrongly titled
    parent. Callers turn that into a skipped write, never into a page in the
    wrong place.
    """
    target = (parent_id or "").strip()
    if not target:
        raise OutsideDashboard("notion parent is not set; run ensure after connecting")
    if not await _is_dashboard(session, target):
        raise OutsideDashboard(f"parent {target} is not the {DASHBOARD_TITLE} page")
    return target


async def ensure_archive(session: NotionSession, dashboard_id: str) -> str:
    """The page generated documents go under, created only when it is missing.

    Deliberately not `upsert_child`: replacing the body of a page that already
    holds documents is how you lose them. When creation gives us no id the
    caller still gets the dashboard, so the write lands one level too high
    rather than not at all.
    """
    existing = await find_child(
        session, parent_id=dashboard_id, title=ARCHIVE_PAGE.title
    )
    if existing is not None:
        return str(existing["id"])
    page_id, _ = await session.create_page(
        title=ARCHIVE_PAGE.title,
        markdown=ARCHIVE_PAGE.body,
        parent_id=dashboard_id,
        icon=ARCHIVE_PAGE.icon,
    )
    return page_id or dashboard_id


async def upsert_child(
    session: NotionSession, *, parent_id: str, title: str, markdown: str
) -> tuple[str | None, str | None, bool]:
    """Write `title` under `parent_id`, replacing the body if it already exists.

    Returns `(page_id, page_url, created)`. Generated documents carry a version
    in their title, so they never collide here; the per-project briefs under one
    hub do, and re-running that hub refreshes them instead of duplicating them.
    """
    existing = await find_child(session, parent_id=parent_id, title=title)
    if existing is not None:
        page_id, page_url = await session.update_page(existing["id"], markdown, title)
        return page_id, page_url or existing.get("url"), False
    page_id, page_url = await session.create_page(
        title=title, markdown=markdown, parent_id=parent_id
    )
    return page_id, page_url, True


async def child_titles(session: NotionSession, parent_id: str) -> list[str]:
    """`parent_id` 바로 아래 페이지 제목들. 읽지 못하면 빈 목록을 준다."""
    try:
        fetched = await session.read_page(parent_id)
    except Exception:
        return []
    return [str(child.get("title") or "") for child in children_from(fetched)]


def next_version(titles: list[str], base: str) -> int:
    """`base v<n> …` 중 가장 큰 n 의 다음 번호.

    개수를 세지 않는다. 사용자가 중간 버전을 지우면 개수가 뒤로 밀려, 아직
    남아 있는 페이지 제목과 부딪히고 그 페이지를 덮어쓴다.
    """
    highest = 0
    for title in titles:
        if not title.startswith(base):
            continue
        match = _VERSION.match(title[len(base):])
        if match:
            highest = max(highest, int(match.group(1)))
    return highest + 1


async def find_child(
    session: NotionSession, *, parent_id: str, title: str
) -> dict[str, Any] | None:
    """An existing child of `parent_id` titled exactly `title`.

    Reads the parent. Does not search the workspace. `None` when there is none
    *or* when the parent could not be read: failing to find means we create,
    which is worse than updating but better than dropping the write.
    """
    try:
        fetched = await session.read_page(parent_id)
    except Exception:
        return None
    return next(
        (child for child in children_from(fetched) if child.get("title") == title),
        None,
    )


async def _find_at_private_root(session: NotionSession) -> DashboardRef | None:
    """The dashboard sitting in the private sidebar, if we can list that sidebar."""
    try:
        hits = await session.list_root_pages()
    except Exception:
        return None
    hit = next(
        (hit for hit in hits if is_dashboard_title(str(hit.get("title") or ""))),
        None,
    )
    if hit is None:
        return None
    return DashboardRef(page_id=str(hit["id"]), page_url=hit.get("url"))


async def _build(session: NotionSession) -> DashboardRef:
    """Create the §3 tree at the private root, parent omitted to mean 'root'."""
    page_id, page_url = await session.create_page(
        title=DASHBOARD_TITLE,
        markdown=render_dashboard_body(),
        parent_id=None,
        icon=DASHBOARD_ICON,
    )
    if not page_id:
        raise RuntimeError("notion create returned no id for the dashboard")

    urls: dict[str, str] = {}
    for child in CHILD_PAGES:
        child_id, child_url = await session.create_page(
            title=child.title,
            markdown=child.body,
            parent_id=page_id,
            icon=child.icon,
        )
        if child_url:
            urls[child.title] = child_url
        elif child_id:
            urls[child.title] = f"https://notion.so/{child_id}"
    await session.update_page(page_id, render_dashboard_body(urls))
    return DashboardRef(page_id=page_id, page_url=page_url, created=True)


async def _ensure_children(session: NotionSession, parent_id: str) -> None:
    """Create any template page the user deleted, leave the rest untouched."""
    for child in CHILD_PAGES:
        if await find_child(session, parent_id=parent_id, title=child.title) is None:
            await session.create_page(
                title=child.title,
                markdown=child.body,
                parent_id=parent_id,
                icon=child.icon,
            )


async def _is_dashboard(session: NotionSession, page_id: str) -> bool:
    try:
        return is_dashboard_title(title_of(await session.read_page(page_id)))
    except Exception:
        return False
