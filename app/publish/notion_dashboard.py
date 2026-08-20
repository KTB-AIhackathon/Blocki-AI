"""Find, build and guard the one page this agent is allowed to write into.

The rule from `Docs/노션템플릿.md` §2: a single `Developer TIL Dashboard`
directly under the user's private root, and nothing outside it is ours. A
user's workspace is not scratch space, so every write here proves it is landing
inside that page before it happens.

Finding the dashboard lists the private root only. Finding a child reads that
dashboard. Workspace-wide search is not used: it walks pages we do not own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.publish.notion_mcp import NotionSession
from app.publish.notion_schema import children_from, title_of
from app.publish.notion_template import (
    CHILD_PAGES,
    DASHBOARD_ICON,
    DASHBOARD_TITLE,
    is_dashboard_title,
    render_dashboard_body,
)

__all__ = [
    "DashboardRef",
    "OutsideDashboard",
    "ensure_dashboard",
    "find_child",
    "guard_parent",
    "upsert_child",
]


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


async def upsert_child(
    session: NotionSession, *, parent_id: str, title: str, markdown: str
) -> tuple[str | None, str | None, bool]:
    """Write `title` under the dashboard, replacing the body if it already exists.

    Returns `(page_id, page_url, created)`. Re-running a job on the same day
    therefore refreshes one page instead of stacking near-duplicates.
    """
    existing = await find_child(session, parent_id=parent_id, title=title)
    if existing is not None:
        page_id, page_url = await session.update_page(existing["id"], markdown, title)
        return page_id, page_url or existing.get("url"), False
    page_id, page_url = await session.create_page(
        title=title, markdown=markdown, parent_id=parent_id
    )
    return page_id, page_url, True


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
