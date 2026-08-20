"""One test per pipeline, asserting the whole intended flow rather than a step.

Every test here goes in through `POST /internal/jobs` and comes out the other
side with a Notion page, running the real collect, analyze, build, render and
publish code. Only the two remote servers are doubles, and the Notion double
speaks the schema the live server advertised, so a shape mistake fails here.

Per-step behaviour belongs in the focused files; this file only answers "does
each pipeline do the job it exists to do".
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.contracts import CollectPolicy
from app.main import create_app
from tests.conftest import NOTION_TOKEN, PAT, FakeGitHub, commit, repo_meta
from tests.notion_double import LIVE_CREATE_PAGES, NotionWorkspace, mcp_session

KEY = "dev-internal-key"
HEADERS = {"X-Internal-Key": KEY, "X-GitHub-Pat": PAT, "X-Notion-Token": NOTION_TOKEN}

#: What `ensure` handed Spring. Every job parents its page here and nowhere else.
DASHBOARD_ID = "page-1"

FIELDS = {
    "name": "홍길동",
    "contact_md": "- me@example.com",
    "experience_md": "- 2025 ~ : 백엔드 엔지니어",
    "education_md": "- 2020 ~ 2024: 컴퓨터공학",
}


@pytest.fixture
def github(monkeypatch: pytest.MonkeyPatch) -> FakeGitHub:
    fake = FakeGitHub(
        list_repos=[{"full_name": "acme/demo"}, {"full_name": "acme/toy"}],
        list_commits=[
            commit("aaa111aaa111", "feat: add job pipeline", days=2),
            commit("bbb222bbb222", "fix: guard empty snapshot", days=3),
            commit("ccc333ccc333", "chore: bump deps", author="carol", days=4),
        ],
        get_repo_meta=repo_meta(),
    )
    real = __import__("app.collect.github", fromlist=["collect_github"]).collect_github

    async def collect(req, github_pat, **_kwargs):
        return await real(req, github_pat, call_tool=fake)

    monkeypatch.setattr("app.api.jobs.collect_github", collect)
    return fake


@pytest.fixture
def notion(monkeypatch: pytest.MonkeyPatch) -> NotionWorkspace:
    """A workspace that already holds the dashboard, as it does after `ensure`."""
    workspace = NotionWorkspace()
    workspace.seed_dashboard()

    async def open_session(token: str):
        assert token == NOTION_TOKEN, "the header token must reach the transport"
        return mcp_session(workspace)

    monkeypatch.setattr("app.publish.notion.open_session", open_session)
    return workspace


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("INTERNAL_API_KEY", KEY)
    return TestClient(create_app())


def run(client: TestClient, **body) -> dict:
    payload = {
        "job_id": "j1",
        "user_id": "u1",
        "notion": {"parent_id": DASHBOARD_ID, "log_date": "2026-08-20"},
        **body,
    }
    response = client.post("/internal/jobs", json=payload, headers=HEADERS)
    assert response.status_code == 200, response.text
    assert PAT not in response.text and NOTION_TOKEN not in response.text
    return response.json()


def document(kind: str, **overrides) -> dict:
    fields = FIELDS | overrides
    return {
        "job_type": kind,
        "repos": [{"owner": "acme", "name": "demo"}],
        "document": {"kind": kind, "profile_fields": fields},
    }


def policy_of(job_type: str) -> CollectPolicy:
    from app import pipelines

    pipeline = pipelines.resolve(job_type)
    assert pipeline is not None
    return pipeline.policy


# --------------------------------------------------------------------------
# portfolio: full history, project-led, no career fields required
# --------------------------------------------------------------------------


def test_portfolio_flows_from_github_to_spring_and_notion(
    client: TestClient, github: FakeGitHub, notion: NotionWorkspace
) -> None:
    body = run(client, **document("portfolio"))

    assert body["ok"] is True
    assert body["proposal"]["status"] == "proposed"

    markdown = body["artifact"]["body_markdown"]
    assert body["artifact"]["kind"] == "portfolio"
    assert "홍길동" in markdown
    assert "## 프로젝트" in markdown
    assert "https://github.com/acme/demo" in markdown

    assert body["notion"] == {
        "attempted": True,
        "ok": True,
        "page_id": "page-2",
        "page_url": "https://notion.so/page-2",
        "skipped_reason": None,
        "error": None,
    }
    page = notion.logs[0]
    assert page["title"] == "포트폴리오 2026-08-20"
    assert page["parent_id"] == DASHBOARD_ID
    assert page["markdown"] == markdown, "Notion and Spring must get the same bytes"


def test_portfolio_reads_full_history_and_ignores_the_cursor(
    client: TestClient, github: FakeGitHub, notion: NotionWorkspace
) -> None:
    body = document("portfolio") | {
        "cursor": [
            {
                "owner": "acme",
                "name": "demo",
                "head_sha": "aaa111aaa111",
                "last_success_at": "2026-05-01T00:00:00Z",
            }
        ]
    }
    result = run(client, **body)

    assert policy_of("portfolio").use_cursor is False
    assert "## 프로젝트" in result["artifact"]["body_markdown"]
    assert all("since_sha" not in args for args in github.args_for("list_commits"))


def test_portfolio_needs_no_career_fields(
    client: TestClient, github: FakeGitHub, notion: NotionWorkspace
) -> None:
    body = run(
        client,
        job_type="portfolio",
        repos=[{"owner": "acme", "name": "demo"}],
        document={"kind": "portfolio", "profile_fields": {"name": "홍길동"}},
    )
    assert body["ok"] is True
    assert len(notion.logs) == 1


# --------------------------------------------------------------------------
# resume: same snapshot, different document, career fields mandatory
# --------------------------------------------------------------------------


def test_resume_flows_from_github_to_spring_and_notion(
    client: TestClient, github: FakeGitHub, notion: NotionWorkspace
) -> None:
    body = run(client, **document("resume"))

    assert body["ok"] is True
    markdown = body["artifact"]["body_markdown"]
    assert body["artifact"]["kind"] == "resume"
    assert "2025 ~ : 백엔드 엔지니어" in markdown
    assert "컴퓨터공학" in markdown

    assert notion.logs[0]["title"] == "이력서 2026-08-20"
    assert notion.logs[0]["markdown"] == markdown


def test_resume_without_a_career_reaches_notion_with_a_blank_to_fill_in(
    client: TestClient, github: FakeGitHub, notion: NotionWorkspace
) -> None:
    body = run(
        client,
        job_type="resume",
        repos=[{"owner": "acme", "name": "demo"}],
        document={"kind": "resume", "profile_fields": {"name": "홍길동"}},
    )

    assert body["ok"] is True
    assert body["proposal"]["status"] == "partial"
    assert set(body["proposal"]["unresolved_fields"]) >= {"experience_md", "education_md"}

    markdown = body["artifact"]["body_markdown"]
    assert "## 경력" in markdown and "## 학력" in markdown
    assert markdown.count("이 Notion 페이지에서 직접 채워주세요") == 2
    assert notion.logs[0]["markdown"] == markdown


def test_a_document_without_a_name_is_blocked_and_never_reaches_notion(
    client: TestClient, github: FakeGitHub, notion: NotionWorkspace
) -> None:
    body = run(
        client,
        job_type="resume",
        repos=[{"owner": "acme", "name": "demo"}],
        document={"kind": "resume", "profile_fields": {"contact_md": "- me@a.com"}},
    )

    assert body["ok"] is False
    assert body["proposal"]["status"] == "blocked"
    assert body["proposal"]["unresolved_fields"] == ["name"]
    assert body["artifact"] is None
    assert body["notion"] is None
    assert notion.logs == []


def test_portfolio_and_resume_are_different_documents_from_one_snapshot(
    client: TestClient, github: FakeGitHub, notion: NotionWorkspace
) -> None:
    one = run(client, **document("portfolio"))["artifact"]["body_markdown"]
    two = run(client, **document("resume"))["artifact"]["body_markdown"]

    assert one != two
    assert one.startswith("# 홍길동") and "## 프로젝트" in one
    assert two.startswith("# 홍길동") and "## 주요 작업" in two
    assert "## 프로젝트" not in two and "## 주요 작업" not in one
    assert [page["title"] for page in notion.logs] == [
        "포트폴리오 2026-08-20",
        "이력서 2026-08-20",
    ]


# --------------------------------------------------------------------------
# progress: incremental, cursor-driven, no profile fields
# --------------------------------------------------------------------------


def test_progress_flows_and_advances_the_cursor(
    client: TestClient, github: FakeGitHub, notion: NotionWorkspace
) -> None:
    body = run(client, job_type="progress_summary", repos=[{"owner": "acme", "name": "demo"}])

    assert body["ok"] is True
    assert body["artifact"]["kind"] == "progress"
    assert body["next_cursor"][0]["head_sha"]
    assert policy_of("progress_summary").use_cursor is True
    assert notion.logs[0]["title"].startswith("진행 메모")


def test_progress_with_an_up_to_date_cursor_refetches_no_commits(
    client: TestClient, github: FakeGitHub, notion: NotionWorkspace
) -> None:
    first = run(client, job_type="progress_summary", repos=[{"owner": "acme", "name": "demo"}])
    before = len(github.args_for("list_commits"))
    body = run(
        client,
        job_type="progress_summary",
        repos=[{"owner": "acme", "name": "demo"}],
        cursor=first["next_cursor"],
    )

    assert body["ok"] is True
    assert body["proposal"]["status"] == "no_change"
    assert len(github.args_for("list_commits")) == before, "head is unchanged, so do not refetch"


# --------------------------------------------------------------------------
# readme: proposes, never writes; the write is a separate approved execution
# --------------------------------------------------------------------------


def test_readme_proposes_without_touching_the_repository(
    client: TestClient, github: FakeGitHub, notion: NotionWorkspace
) -> None:
    body = run(
        client,
        job_type="readme_proposal",
        repos=[{"owner": "acme", "name": "demo"}],
        readme={"owner": "acme", "repo": "demo", "path": "README.md"},
    )

    assert body["ok"] is True
    assert body["artifact"]["kind"] == "readme"
    action = body["proposal"]["proposed_action"]
    assert action["type"] == "create_readme_pr"
    assert (action["owner"], action["repo"], action["path"]) == ("acme", "demo", "README.md")
    assert action["expected_blob_sha"] == "blob1"
    assert body["proposal"]["action_digest"]
    for write in ("create_branch", "update_file", "create_pr"):
        assert not github.called(write), f"{write} must wait for an approved execution"


def test_readme_proposal_is_also_logged_to_notion(
    client: TestClient, github: FakeGitHub, notion: NotionWorkspace
) -> None:
    body = run(
        client,
        job_type="readme_proposal",
        repos=[{"owner": "acme", "name": "demo"}],
        readme={"owner": "acme", "repo": "demo", "path": "README.md"},
    )
    assert notion.logs[0]["markdown"] == body["artifact"]["body_markdown"]


# --------------------------------------------------------------------------
# cross-cutting: the parts that must hold for every pipeline
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        document("portfolio"),
        document("resume"),
        {"job_type": "progress_summary", "repos": [{"owner": "acme", "name": "demo"}]},
        {
            "job_type": "readme_proposal",
            "repos": [{"owner": "acme", "name": "demo"}],
            "readme": {"owner": "acme", "repo": "demo", "path": "README.md"},
        },
    ],
    ids=["portfolio", "resume", "progress", "readme"],
)
def test_every_pipeline_sends_spring_and_notion_the_same_document(
    client: TestClient, github: FakeGitHub, notion: NotionWorkspace, body: dict
) -> None:
    result = run(client, **body)

    assert result["ok"] is True
    assert result["artifact"]["content_type"] == "text/markdown"
    assert result["proposal"]["proposal_digest"]
    assert result["notion"]["ok"] is True
    assert notion.logs[0]["markdown"] == result["artifact"]["body_markdown"]
    assert notion.logs[0]["parent_id"] == DASHBOARD_ID


def test_a_notion_outage_still_delivers_the_document_to_spring(
    client: TestClient, github: FakeGitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def open_session(_token: str):
        raise RuntimeError(f"503 from notion for {NOTION_TOKEN}")

    monkeypatch.setattr("app.publish.notion.open_session", open_session)
    body = run(client, **document("portfolio"))

    assert body["ok"] is True
    assert body["artifact"]["body_markdown"]
    assert body["notion"]["ok"] is False
    assert body["notion"]["error"]["retryable"] is True


def test_a_notion_schema_we_cannot_fill_is_reported_not_guessed(
    client: TestClient, github: FakeGitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Silently dropping the parent would file the page in the wrong place."""
    workspace = NotionWorkspace()
    workspace.seed_dashboard()
    session = mcp_session(workspace)
    session.tools["notion-create-pages"].args_schema = {
        "type": "object",
        "properties": {"pages": LIVE_CREATE_PAGES["properties"]["pages"]},
        "required": ["pages"],
    }

    async def open_session(_token: str):
        return session

    monkeypatch.setattr("app.publish.notion.open_session", open_session)
    body = run(client, **document("portfolio"))

    assert body["ok"] is True
    assert body["notion"]["ok"] is False
    assert workspace.logs == []
