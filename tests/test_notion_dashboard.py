"""The dashboard rules from `Docs/노션템플릿.md` §2, §3 and §7.

Two of these are the whole point of the change: nothing is written outside the
dashboard, and re-running a job on the same day refreshes one page instead of
stacking copies.
"""

from __future__ import annotations

import pytest

from app.publish import notion_dashboard as dash
from app.publish.notion_template import (
    ARCHIVE_TITLE,
    CHILD_PAGES,
    DAILY_TEMPLATE_TITLE,
    DASHBOARD_ICON,
    DASHBOARD_TITLE,
    EVIDENCE_HEADING,
    is_dashboard_title,
)
from tests.notion_double import NotionWorkspace


# -- ensure ------------------------------------------------------------------


def test_the_icon_prefix_still_counts_as_the_dashboard_title() -> None:
    assert is_dashboard_title("🧑‍💻 Developer TIL Dashboard")
    assert is_dashboard_title(DASHBOARD_TITLE)
    assert not is_dashboard_title("팀 Developer TIL Dashboard")


async def test_an_empty_workspace_gets_the_whole_template_tree() -> None:
    workspace = NotionWorkspace()

    ref = await dash.ensure_dashboard(workspace)

    assert ref.created is True
    root = [page for page in workspace.pages if page["parent_id"] is None]
    assert [page["title"] for page in root] == [DASHBOARD_TITLE]
    assert workspace.titles_under(ref.page_id) == [child.title for child in CHILD_PAGES]


async def test_the_tree_is_the_template_and_the_archive_and_nothing_else() -> None:
    """예시 TIL 페이지는 더 이상 만들지 않는다. 사용자가 지운 뒤 다시 생기던 것들이다."""
    workspace = NotionWorkspace()
    ref = await dash.ensure_dashboard(workspace)

    assert workspace.titles_under(ref.page_id) == [DAILY_TEMPLATE_TITLE, ARCHIVE_TITLE]


async def test_the_daily_template_keeps_the_evidence_heading() -> None:
    workspace = NotionWorkspace()
    ref = await dash.ensure_dashboard(workspace)
    template = next(
        page
        for page in workspace.pages
        if page["parent_id"] == ref.page_id and page["title"] == DAILY_TEMPLATE_TITLE
    )
    assert EVIDENCE_HEADING in template["markdown"]


async def test_the_dashboard_asks_for_its_icon() -> None:
    workspace = NotionWorkspace()
    await dash.ensure_dashboard(workspace)
    assert workspace.creates[0]["icon"] == DASHBOARD_ICON


async def test_an_existing_dashboard_is_reused_not_duplicated() -> None:
    workspace = NotionWorkspace()
    existing = workspace.seed_dashboard()

    ref = await dash.ensure_dashboard(workspace)

    assert (ref.page_id, ref.created) == (existing, False)
    assert [item["title"] for item in workspace.creates if item["parent_id"] is None] == []
    assert workspace.titles_under(existing) == [child.title for child in CHILD_PAGES]


async def test_a_known_id_skips_the_private_root_listing() -> None:
    workspace = NotionWorkspace()
    existing = workspace.seed_dashboard()

    ref = await dash.ensure_dashboard(workspace, known_page_id=existing)

    assert ref.page_id == existing
    assert workspace.searches == []
    assert workspace.root_lists == 0


async def test_a_stale_known_id_falls_back_to_the_private_root() -> None:
    workspace = NotionWorkspace()
    real = workspace.seed_dashboard()

    ref = await dash.ensure_dashboard(workspace, known_page_id="page-does-not-exist")

    assert ref.page_id == real
    assert workspace.root_lists == 1
    assert workspace.searches == []


async def test_a_deleted_template_child_is_recreated_from_markdown() -> None:
    workspace = NotionWorkspace()
    dashboard = workspace.seed_dashboard()
    workspace.seed(ARCHIVE_TITLE, parent_id=dashboard, body="사람이 고친 안내문")

    await dash.ensure_dashboard(workspace, known_page_id=dashboard)

    assert workspace.body_of(
        next(page["id"] for page in workspace.pages if page["title"] == ARCHIVE_TITLE)
    ) == "사람이 고친 안내문"
    assert DAILY_TEMPLATE_TITLE in workspace.titles_under(dashboard)


async def test_a_same_titled_page_nested_elsewhere_is_not_adopted() -> None:
    workspace = NotionWorkspace()
    other = workspace.seed("팀 위키")
    workspace.seed(DASHBOARD_TITLE, parent_id=other)

    ref = await dash.ensure_dashboard(workspace)

    assert ref.created is True
    assert ref.page_id != other


# -- parent guard ------------------------------------------------------------


async def test_a_write_aimed_at_the_dashboard_is_allowed() -> None:
    workspace = NotionWorkspace()
    dashboard = workspace.seed_dashboard()

    assert await dash.guard_parent(workspace, dashboard) == dashboard


async def test_a_write_aimed_at_the_oauth_workspace_id_is_refused() -> None:
    """The bug this guard exists for: a workspace id is not a page id."""
    workspace = NotionWorkspace()
    workspace.seed_dashboard()

    with pytest.raises(dash.OutsideDashboard):
        await dash.guard_parent(workspace, "workspace-abc123")


async def test_a_write_aimed_at_some_other_page_is_refused() -> None:
    workspace = NotionWorkspace()
    workspace.seed_dashboard()
    elsewhere = workspace.seed("개인 일기")

    with pytest.raises(dash.OutsideDashboard):
        await dash.guard_parent(workspace, elsewhere)


async def test_an_unset_parent_is_refused_rather_than_defaulting_to_the_root() -> None:
    workspace = NotionWorkspace()
    with pytest.raises(dash.OutsideDashboard):
        await dash.guard_parent(workspace, None)


# -- archive -----------------------------------------------------------------


async def test_the_archive_page_is_created_once_and_then_reused() -> None:
    workspace = NotionWorkspace()
    dashboard = workspace.seed_dashboard()

    first = await dash.ensure_archive(workspace, dashboard)
    second = await dash.ensure_archive(workspace, dashboard)

    assert first == second
    assert workspace.titles_under(dashboard) == [ARCHIVE_TITLE]


async def test_the_archive_body_survives_a_second_run() -> None:
    """생성된 문서를 담고 있는 페이지의 본문을 덮어쓰면 그 아래 문서가 사라진다."""
    workspace = NotionWorkspace()
    dashboard = workspace.seed_dashboard()
    archive = workspace.seed(ARCHIVE_TITLE, parent_id=dashboard, body="사람이 고친 안내문")

    assert await dash.ensure_archive(workspace, dashboard) == archive
    assert workspace.body_of(archive) == "사람이 고친 안내문"
    assert workspace.creates == []


# -- upsert ------------------------------------------------------------------


async def test_the_first_write_of_the_day_creates_one_page() -> None:
    workspace = NotionWorkspace()
    dashboard = workspace.seed_dashboard()

    page_id, _, created = await dash.upsert_child(
        workspace, parent_id=dashboard, title="이력서 2026-08-20", markdown="# 첫 판"
    )

    assert created is True
    assert workspace.titles_under(dashboard) == ["이력서 2026-08-20"]
    assert workspace.body_of(page_id) == "# 첫 판"


async def test_rerunning_the_same_day_replaces_the_body_and_adds_no_page() -> None:
    workspace = NotionWorkspace()
    dashboard = workspace.seed_dashboard()
    first, _, _ = await dash.upsert_child(
        workspace, parent_id=dashboard, title="이력서 2026-08-20", markdown="# 첫 판"
    )

    second, _, created = await dash.upsert_child(
        workspace, parent_id=dashboard, title="이력서 2026-08-20", markdown="# 두번째 판"
    )

    assert (second, created) == (first, False)
    assert workspace.titles_under(dashboard) == ["이력서 2026-08-20"]
    assert workspace.body_of(first) == "# 두번째 판"


async def test_a_same_titled_page_under_another_parent_is_not_touched() -> None:
    workspace = NotionWorkspace()
    dashboard = workspace.seed_dashboard()
    elsewhere = workspace.seed("남의 페이지")
    stranger = workspace.seed("이력서 2026-08-20", parent_id=elsewhere, body="건드리지 마")

    await dash.upsert_child(
        workspace, parent_id=dashboard, title="이력서 2026-08-20", markdown="# 내 것"
    )

    assert workspace.body_of(stranger) == "건드리지 마"
    assert workspace.titles_under(dashboard) == ["이력서 2026-08-20"]


async def test_without_search_the_write_still_lands() -> None:
    """Child lookup reads the dashboard. Workspace search is not required."""
    workspace = NotionWorkspace(searchable=False)
    dashboard = workspace.seed_dashboard()

    _, _, created = await dash.upsert_child(
        workspace, parent_id=dashboard, title="이력서 2026-08-20", markdown="# 본문"
    )

    assert created is True
    assert workspace.titles_under(dashboard) == ["이력서 2026-08-20"]
    assert workspace.searches == []


async def test_dated_portfolio_logs_stack_under_the_dashboard_only() -> None:
    workspace = NotionWorkspace()
    dashboard = workspace.seed_dashboard()

    await dash.upsert_child(
        workspace, parent_id=dashboard, title="포트폴리오 2026-08-19", markdown="# 어제"
    )
    await dash.upsert_child(
        workspace, parent_id=dashboard, title="포트폴리오 2026-08-20", markdown="# 오늘"
    )

    assert workspace.titles_under(dashboard) == [
        "포트폴리오 2026-08-19",
        "포트폴리오 2026-08-20",
    ]
    assert workspace.searches == []


class _ConflictThenPresent(NotionWorkspace):
    def __init__(self) -> None:
        super().__init__()
        self.attempts = 0

    async def create_page(self, *, title: str, markdown: str, parent_id: str | None, icon=None):
        self.attempts += 1
        if self.attempts == 1:
            self.seed(title, parent_id=parent_id, body="old")
            raise RuntimeError("notion api 409 /pages: conflict")
        return await super().create_page(
            title=title, markdown=markdown, parent_id=parent_id, icon=icon
        )


class _ConflictMissing(NotionWorkspace):
    def __init__(self) -> None:
        super().__init__()
        self.attempts = 0

    async def create_page(self, *, title: str, markdown: str, parent_id: str | None, icon=None):
        self.attempts += 1
        if self.attempts == 1:
            raise RuntimeError("notion api 409 /pages: conflict")
        return await super().create_page(
            title=title, markdown=markdown, parent_id=parent_id, icon=icon
        )


async def test_upsert_child_refinds_and_updates_after_create_conflict() -> None:
    workspace = _ConflictThenPresent()
    parent = workspace.seed("parent")

    page_id, _, created = await dash.upsert_child(
        workspace, parent_id=parent, title="demo", markdown="# new\n"
    )

    assert created is False
    assert workspace.attempts == 1
    assert workspace.body_of(page_id) == "# new\n"


async def test_upsert_child_retries_create_once_when_conflict_has_no_child() -> None:
    workspace = _ConflictMissing()
    parent = workspace.seed("parent")

    page_id, _, created = await dash.upsert_child(
        workspace, parent_id=parent, title="demo", markdown="# new\n"
    )

    assert created is True
    assert workspace.attempts == 2
    assert workspace.body_of(page_id) == "# new\n"
