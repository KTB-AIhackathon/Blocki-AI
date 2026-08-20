from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from app.contracts import ArtifactPayload, NotionTarget
from app.publish import notion
from app.publish.notion_mcp import McpSession
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
    assert fake.logs[0]["title"] == "이력서 2026-08-20"
    assert fake.logs[0]["parent_id"] == fake.parent


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


async def test_a_second_run_on_the_same_day_updates_one_page() -> None:
    fake = FakeSession()
    target = fake.target(log_date=date(2026, 8, 20))

    first = await notion.publish_artifact(
        artifact(body="# 첫 판"), notion_token=NOTION_TOKEN, target=target, session=fake
    )
    second = await notion.publish_artifact(
        artifact(body="# 두번째 판"), notion_token=NOTION_TOKEN, target=target, session=fake
    )

    assert first.page_id == second.page_id
    assert fake.titles_under(fake.parent) == ["이력서 2026-08-20"]
    assert fake.body_of(second.page_id) == "# 두번째 판"


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


async def test_read_page_uses_the_advertised_argument_name() -> None:
    sink: list = []
    schema = {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}
    session = McpSession(tools={"notion-fetch": _tool(schema, sink)})

    await session.read_page("https://notion.so/page-9")

    assert sink[0]["args"] == {"url": "https://notion.so/page-9"}
