from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from app.contracts import ArtifactPayload, NotionTarget
from app.publish import notion
from app.publish.notion_mcp import McpSession
from app.publish.notion_template import ARCHIVE_TITLE
from tests.conftest import NOTION_TOKEN
from tests.notion_double import LIVE_CREATE_PAGES, NotionWorkspace, mcp_session


def artifact(kind: str = "resume", body: str = "# 이력서\n") -> ArtifactPayload:
    return ArtifactPayload(kind=kind, title="이력서", body_markdown=body, proposal_id="p1")


class FakeSession(NotionWorkspace):
    """A connected workspace: the dashboard exists and `parent` points at it."""

    def __init__(self, page=None, error: Exception | None = None) -> None:
        super().__init__()
        self.parent = self.seed_dashboard()
        self.page = page
        self.error = error

    async def create_page(self, *, title: str, markdown: str, parent_id: str | None, icon=None):
        if self.error is not None:
            raise self.error
        if self.page is not None:
            return self.page
        return await super().create_page(
            title=title, markdown=markdown, parent_id=parent_id, icon=icon
        )

    def target(self, **overrides) -> NotionTarget:
        return NotionTarget(parent_id=self.parent, **overrides)


async def test_publishes_with_a_date_stamped_title() -> None:
    fake = FakeSession()
    result = await notion.publish_artifact(
        artifact(),
        notion_token=NOTION_TOKEN,
        target=fake.target(log_date=date(2026, 8, 20)),
        session=fake,
    )

    assert result.ok is True
    assert result.page_id == fake.logs[0]["id"]
    assert fake.logs[0]["title"] == "이력서 v1 2026-08-20"
    # 대시보드에는 보관 페이지만, 문서는 그 아래에.
    assert fake.titles_under(fake.parent) == [ARCHIVE_TITLE]
    assert fake.logs[0]["parent_id"] == fake.archive_id


async def test_explicit_title_wins_over_the_date_stamp() -> None:
    fake = FakeSession()
    await notion.publish_artifact(
        artifact(),
        notion_token=NOTION_TOKEN,
        target=fake.target(title="2026 상반기 이력서"),
        session=fake,
    )
    assert fake.logs[0]["title"] == "2026 상반기 이력서"


async def test_a_parent_outside_the_dashboard_is_skipped_not_written() -> None:
    fake = FakeSession()
    elsewhere = fake.seed("개인 일기")

    result = await notion.publish_artifact(
        artifact(),
        notion_token=NOTION_TOKEN,
        target=NotionTarget(parent_id=elsewhere),
        session=fake,
    )

    assert (result.attempted, result.ok) == (False, False)
    assert result.skipped_reason is not None and elsewhere in result.skipped_reason
    assert fake.titles_under(elsewhere) == []


async def test_a_second_run_on_the_same_day_stacks_a_new_version() -> None:
    """웹 아카이브가 판마다 한 줄씩 남기므로 Notion 도 덮어쓰지 않고 쌓는다."""
    fake = FakeSession()
    target = fake.target(log_date=date(2026, 8, 20))

    first = await notion.publish_artifact(
        artifact(body="# 첫 판"), notion_token=NOTION_TOKEN, target=target, session=fake
    )
    second = await notion.publish_artifact(
        artifact(body="# 두번째 판"), notion_token=NOTION_TOKEN, target=target, session=fake
    )

    assert first.page_id != second.page_id
    assert fake.titles_under(fake.archive_id) == [
        "이력서 v1 2026-08-20",
        "이력서 v2 2026-08-20",
    ]
    assert fake.body_of(first.page_id) == "# 첫 판"
    assert fake.body_of(second.page_id) == "# 두번째 판"


async def test_a_deleted_middle_version_does_not_overwrite_a_later_one() -> None:
    """개수를 세면 v2 를 지웠을 때 다음 판이 v2 가 되어 v3 을 덮어쓴다."""
    fake = FakeSession()
    target = fake.target(log_date=date(2026, 8, 20))
    for _ in range(3):
        await notion.publish_artifact(
            artifact(), notion_token=NOTION_TOKEN, target=target, session=fake
        )
    stale = next(page for page in fake.logs if page["title"] == "이력서 v2 2026-08-20")
    fake.pages.remove(stale)

    await notion.publish_artifact(
        artifact(body="# 네번째 판"), notion_token=NOTION_TOKEN, target=target, session=fake
    )

    assert fake.titles_under(fake.archive_id) == [
        "이력서 v1 2026-08-20",
        "이력서 v3 2026-08-20",
        "이력서 v4 2026-08-20",
    ]


async def test_the_log_date_defaults_to_kst_not_the_server_clock() -> None:
    assert notion.kst_today() == notion.utcnow().astimezone(notion.KST).date()


async def test_missing_token_is_skipped_not_failed() -> None:
    result = await notion.publish_artifact(artifact(), notion_token="", target=None)
    assert (result.attempted, result.ok, result.skipped_reason) == (False, False, "missing_token")


async def test_empty_markdown_is_skipped() -> None:
    fake = FakeSession()
    result = await notion.publish_artifact(
        artifact(body="   "), notion_token=NOTION_TOKEN, target=fake.target(), session=fake
    )
    assert result.skipped_reason == "no_markdown"


async def test_no_artifact_is_skipped() -> None:
    result = await notion.publish_artifact(None, notion_token=NOTION_TOKEN, target=None)
    assert result.skipped_reason == "no_artifact"


async def test_transport_failure_is_retryable_and_scrubbed() -> None:
    fake = FakeSession(error=RuntimeError(f"401 for token {NOTION_TOKEN}"))
    result = await notion.publish_artifact(
        artifact(), notion_token=NOTION_TOKEN, target=fake.target(), session=fake
    )

    assert result.attempted is True
    assert result.ok is False
    assert result.error is not None and result.error.retryable is True
    assert NOTION_TOKEN not in result.error.message


async def test_a_response_without_a_page_id_is_an_error() -> None:
    fake = FakeSession(page=(None, None))
    result = await notion.publish_artifact(
        artifact(), notion_token=NOTION_TOKEN, target=fake.target(), session=fake
    )
    assert result.ok is False
    assert result.error is not None


def _tool(schema: dict, sink: list):
    async def ainvoke(call):
        sink.append(call)
        return SimpleNamespace(
            artifact={"pages": [{"id": "page-9", "url": "https://notion.so/page-9"}]},
            content="created",
        )

    return SimpleNamespace(args_schema=schema, ainvoke=ainvoke)


async def test_the_mcp_session_sends_the_live_schema_shape() -> None:
    sink: list = []
    session = McpSession(tools={"notion-create-pages": _tool(LIVE_CREATE_PAGES, sink)})

    page = await session.create_page(title="T", markdown="# B", parent_id="parent-1")

    assert page == ("page-9", "https://notion.so/page-9")
    assert sink[0]["name"] == "notion-create-pages"
    assert sink[0]["args"] == {
        "parent": {"page_id": "parent-1"},
        "pages": [{"properties": {"title": "T"}, "content": "# B"}],
    }


async def test_a_server_without_a_create_tool_names_what_it_offered() -> None:
    workspace = NotionWorkspace()
    dashboard = workspace.seed_dashboard()
    session = mcp_session(workspace)
    del session.tools["notion-create-pages"]

    result = await notion.publish_markdown(
        title="T", markdown="B", notion_token=NOTION_TOKEN, parent_id=dashboard, session=session
    )

    assert result.ok is False
    assert result.error is not None and "notion-fetch" in result.error.message


async def test_portfolio_publish_writes_hub_children_and_keeps_page_id_on_the_doc() -> None:
    fake = FakeSession()
    warnings: list[str] = []
    result = await notion.publish_artifact(
        ArtifactPayload(
            kind="portfolio",
            title="포트폴리오",
            body_markdown="# 홍길동\n\n## 프로젝트\n\n### demo\n",
            proposal_id="p1",
        ),
        notion_token=NOTION_TOKEN,
        target=fake.target(log_date=date(2026, 8, 20)),
        session=fake,
        briefs=[{"title": "demo", "markdown": "# demo\n\n- 기간: 2026.08\n"}],
        publish_warnings=warnings,
    )

    folio = next(page for page in fake.logs if page["title"] == "포트폴리오 v1 2026-08-20")
    hub = next(page for page in fake.logs if page["title"] == "프로젝트 v1 2026-08-20")
    assert result.ok is True
    assert result.page_id == folio["id"]
    assert fake.titles_under(hub["id"]) == ["demo"]
    child = next(page for page in fake.pages if page["title"] == "demo")
    assert "날짜:" not in (child["markdown"] or "")
    assert warnings == []


async def test_portfolio_hub_index_appends_unmatched_learning() -> None:
    fake = FakeSession()
    result = await notion.publish_artifact(
        ArtifactPayload(
            kind="portfolio",
            title="포트폴리오",
            body_markdown="# 홍길동\n",
            proposal_id="p1",
        ),
        notion_token=NOTION_TOKEN,
        target=fake.target(log_date=date(2026, 8, 20)),
        session=fake,
        briefs=[{"title": "demo", "markdown": "# demo\n"}],
        hub_tail="## 그 외 학습\n- 2026-08-01 · 혼자 있는 기록\n",
    )
    hub = next(page for page in fake.logs if page["title"] == "프로젝트 v1 2026-08-20")
    body = fake.body_of(hub["id"]) or ""
    assert result.ok is True
    assert "## 그 외 학습" in body
    assert "혼자 있는 기록" in body
    assert "날짜:" not in body
    assert fake.titles_under(hub["id"]) == ["demo"]


async def test_portfolio_publish_rerun_stacks_a_new_version_with_its_own_hub() -> None:
    fake = FakeSession()
    target = fake.target(log_date=date(2026, 8, 20))
    payload = ArtifactPayload(
        kind="portfolio", title="포트폴리오", body_markdown="# 첫\n", proposal_id="p1"
    )
    first = await notion.publish_artifact(
        payload,
        notion_token=NOTION_TOKEN,
        target=target,
        session=fake,
        briefs=[{"title": "demo", "markdown": "# 첫 정리\n"}],
    )
    second = await notion.publish_artifact(
        payload.model_copy(update={"body_markdown": "# 둘\n"}),
        notion_token=NOTION_TOKEN,
        target=target,
        session=fake,
        briefs=[{"title": "demo", "markdown": "# 둘째 정리\n"}],
    )
    assert first.page_id != second.page_id
    # 판마다 문서와 허브가 짝을 이룬다. 이전 판의 정리도 그대로 남는다.
    assert fake.titles_under(fake.archive_id) == [
        "포트폴리오 v1 2026-08-20",
        "프로젝트 v1 2026-08-20",
        "포트폴리오 v2 2026-08-20",
        "프로젝트 v2 2026-08-20",
    ]
    bodies = []
    for version in ("v1", "v2"):
        hub = next(page for page in fake.logs if page["title"] == f"프로젝트 {version} 2026-08-20")
        assert fake.titles_under(hub["id"]) == ["demo"]
        child = next(page for page in fake.pages if page["parent_id"] == hub["id"])
        bodies.append(fake.body_of(child["id"]))
    assert bodies == ["# 첫 정리\n", "# 둘째 정리\n"]


async def test_portfolio_publishes_main_then_hub_then_briefs_serially() -> None:
    fake = FakeSession()
    await notion.publish_artifact(
        ArtifactPayload(
            kind="portfolio",
            title="포트폴리오",
            body_markdown="# 홍길동\n",
            proposal_id="p1",
        ),
        notion_token=NOTION_TOKEN,
        target=fake.target(log_date=date(2026, 8, 20)),
        session=fake,
        briefs=[{"title": "demo", "markdown": "# demo\n"}],
    )
    created = [item["title"] for item in fake.creates]
    folio = created.index("포트폴리오 v1 2026-08-20")
    hub = created.index("프로젝트 v1 2026-08-20")
    brief = created.index("demo")
    assert folio < hub < brief


async def test_secondary_page_failures_keep_main_notion_ok_and_name_the_page() -> None:
    warnings: list[str] = []

    class FailHub(FakeSession):
        async def create_page(self, *, title: str, markdown: str, parent_id: str | None, icon=None):
            if title.startswith("프로젝트"):
                raise RuntimeError("hub write failed")
            return await NotionWorkspace.create_page(
                self, title=title, markdown=markdown, parent_id=parent_id, icon=icon
            )

    fake = FailHub()
    result = await notion.publish_artifact(
        ArtifactPayload(
            kind="portfolio",
            title="포트폴리오",
            body_markdown="# 홍길동\n",
            proposal_id="p1",
        ),
        notion_token=NOTION_TOKEN,
        target=fake.target(log_date=date(2026, 8, 20)),
        session=fake,
        briefs=[{"title": "demo", "markdown": "# demo\n"}],
        publish_warnings=warnings,
    )
    folio = next(page for page in fake.logs if page["title"] == "포트폴리오 v1 2026-08-20")
    assert result.ok is True
    assert result.page_id == folio["id"]
    assert any("허브" in note for note in warnings)


async def test_main_portfolio_failure_keeps_notion_failed() -> None:
    class FailMain(FakeSession):
        async def create_page(self, *, title: str, markdown: str, parent_id: str | None, icon=None):
            if title.startswith("포트폴리오"):
                raise RuntimeError("main write failed")
            return await NotionWorkspace.create_page(
                self, title=title, markdown=markdown, parent_id=parent_id, icon=icon
            )

    fake = FailMain()
    result = await notion.publish_artifact(
        ArtifactPayload(
            kind="portfolio",
            title="포트폴리오",
            body_markdown="# 홍길동\n",
            proposal_id="p1",
        ),
        notion_token=NOTION_TOKEN,
        target=fake.target(log_date=date(2026, 8, 20)),
        session=fake,
        briefs=[{"title": "demo", "markdown": "# demo\n"}],
    )
    assert result.ok is False
    assert result.error is not None


async def test_read_page_uses_the_advertised_argument_name() -> None:
    sink: list = []
    schema = {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}
    session = McpSession(tools={"notion-fetch": _tool(schema, sink)})

    await session.read_page("https://notion.so/page-9")

    assert sink[0]["args"] == {"url": "https://notion.so/page-9"}
