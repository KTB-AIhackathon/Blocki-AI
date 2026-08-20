from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.jobs import handle_job
from app.contracts import DocumentSpec, JobRequest, NotionSnapshot, ProfileFields, TilEntry
from app.main import create_app
from tests.conftest import NOTION_TOKEN, PAT, FakeGitHub

KEY = "dev-internal-key"
HEADERS = {"X-Internal-Key": KEY, "X-GitHub-Pat": PAT}

FIELDS = ProfileFields(
    name="홍길동",
    contact_md="- me@example.com",
    experience_md="- 2025 ~ : 백엔드",
    education_md="- 2020 ~ 2024: 컴퓨터공학",
)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("INTERNAL_API_KEY", KEY)
    return TestClient(create_app())


@pytest.fixture(autouse=True)
def stub_til(monkeypatch: pytest.MonkeyPatch) -> None:
    async def empty(parent_id="", token="", **_kwargs):
        return NotionSnapshot(entries=[], complete=True)

    monkeypatch.setattr("app.api.jobs.collect_notion_til", empty)


@pytest.fixture
def stub_github(monkeypatch: pytest.MonkeyPatch) -> FakeGitHub:
    fake = FakeGitHub()
    real = __import__("app.collect.github", fromlist=["collect_github"]).collect_github

    async def collect(req, github_pat, **kwargs):
        return await real(req, github_pat, call_tool=fake)

    monkeypatch.setattr("app.api.jobs.collect_github", collect)
    return fake


def resume_body() -> dict:
    return {
        "job_id": "j1",
        "user_id": "u1",
        "job_type": "resume",
        "repos": [{"owner": "acme", "name": "demo"}],
        "document": {"kind": "resume", "profile_fields": FIELDS.model_dump()},
    }


def test_health_needs_no_key(client: TestClient) -> None:
    assert client.get("/health").json() == {"ok": True}


def test_missing_internal_key_is_rejected(client: TestClient) -> None:
    response = client.post("/internal/jobs", json=resume_body())
    assert response.status_code == 401


def test_missing_pat_fails_before_any_github_call(client: TestClient) -> None:
    response = client.post(
        "/internal/jobs", json=resume_body(), headers={"X-Internal-Key": KEY}
    )
    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is False
    assert body["error"]["code"] == "missing_pat"
    assert body["proposal"] is None


def test_unsupported_job_type_is_a_422(client: TestClient) -> None:
    body = resume_body() | {"job_type": "invoice"}
    assert client.post("/internal/jobs", json=body, headers=HEADERS).status_code == 422


def test_document_job_without_document_is_a_422(client: TestClient) -> None:
    body = resume_body()
    del body["document"]
    response = client.post("/internal/jobs", json=body, headers=HEADERS)
    assert response.status_code == 422
    assert "document" in response.text


def test_successful_job_returns_artifact_for_spring(
    client: TestClient, stub_github: FakeGitHub
) -> None:
    response = client.post("/internal/jobs", json=resume_body(), headers=HEADERS)
    body = response.json()

    assert body["ok"] is True
    assert body["artifact"]["kind"] == "resume"
    assert body["artifact"]["content_type"] == "text/markdown"
    assert "홍길동" in body["artifact"]["body_markdown"]
    assert body["next_cursor"][0]["head_sha"]
    assert body["snapshot_summary"]["repo_count"] == 1
    assert PAT not in response.text


def test_notion_is_skipped_without_a_token(client: TestClient, stub_github: FakeGitHub) -> None:
    body = client.post("/internal/jobs", json=resume_body(), headers=HEADERS).json()
    assert body["notion"] == {
        "attempted": False,
        "ok": False,
        "page_id": None,
        "page_url": None,
        "skipped_reason": "missing_token",
        "error": None,
    }


def test_notion_fan_out_uses_the_header_token(
    client: TestClient, stub_github: FakeGitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict = {}

    async def publish(artifact, *, notion_token, target, call_tool=None):
        from app.contracts import NotionWriteResult

        seen["token"] = notion_token
        seen["title"] = artifact.title
        seen["parent"] = target.parent_id if target else None
        return NotionWriteResult(
            attempted=True, ok=True, page_id="page-1", page_url="https://notion.so/page-1"
        )

    monkeypatch.setattr("app.api.jobs.publish_artifact", publish)
    payload = resume_body() | {"notion": {"parent_id": "parent-1", "log_date": "2026-08-20"}}
    response = client.post(
        "/internal/jobs", json=payload, headers=HEADERS | {"X-Notion-Token": NOTION_TOKEN}
    )
    body = response.json()

    assert seen["token"] == NOTION_TOKEN
    assert seen["parent"] == "parent-1"
    assert seen["title"] == "이력서"
    assert body["notion"]["page_url"] == "https://notion.so/page-1"
    assert NOTION_TOKEN not in response.text


def test_notion_failure_does_not_fail_the_job(
    client: TestClient, stub_github: FakeGitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def publish(*_args, **_kwargs):
        raise RuntimeError("notion down")

    monkeypatch.setattr("app.api.jobs.publish_artifact", publish)
    response = client.post(
        "/internal/jobs", json=resume_body(), headers=HEADERS | {"X-Notion-Token": NOTION_TOKEN}
    )
    body = response.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "internal"
    assert NOTION_TOKEN not in response.text


def test_blocked_document_is_not_published_to_notion(
    client: TestClient, stub_github: FakeGitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = {"n": 0}

    async def publish(*_args, **_kwargs):
        called["n"] += 1

    monkeypatch.setattr("app.api.jobs.publish_artifact", publish)
    payload = resume_body()
    payload["document"]["profile_fields"] = {"contact_md": "- me@a.com"}
    body = client.post(
        "/internal/jobs", json=payload, headers=HEADERS | {"X-Notion-Token": NOTION_TOKEN}
    ).json()

    assert body["ok"] is False
    assert body["proposal"]["status"] == "blocked"
    assert body["artifact"] is None
    assert called["n"] == 0


async def test_each_job_uses_its_own_pat(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    async def collect(req, github_pat, **_kwargs):
        from app.contracts import GitHubSnapshot, utcnow

        seen.append(github_pat)
        return GitHubSnapshot(
            collected_at=utcnow(),
            complete=True,
            snapshot_digest="d" * 64,
            viewer_login=github_pat[-3:],
        )

    monkeypatch.setattr("app.api.jobs.collect_github", collect)
    one = JobRequest(job_id="1", user_id="u1", job_type="progress_summary")
    two = JobRequest(job_id="2", user_id="u2", job_type="progress_summary")

    await handle_job(one, "pat-one")
    await handle_job(two, "pat-two")
    assert seen == ["pat-one", "pat-two"]


async def test_document_job_ignores_a_stale_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    """The old bug: a cursor from a progress run emptied the projects section."""
    fake = FakeGitHub()
    real = __import__("app.collect.github", fromlist=["collect_github"]).collect_github

    async def collect(req, github_pat, **_kwargs):
        return await real(req, github_pat, call_tool=fake)

    monkeypatch.setattr("app.api.jobs.collect_github", collect)
    job = JobRequest(
        job_id="j1",
        user_id="u1",
        job_type="portfolio",
        repos=[{"owner": "acme", "name": "demo"}],
        cursor=[
            {
                "owner": "acme",
                "name": "demo",
                "head_sha": "abc123def456",
                "last_success_at": "2026-05-01T00:00:00Z",
            }
        ],
        document=DocumentSpec(kind="portfolio", profile_fields=FIELDS),
    )
    result = await handle_job(job, PAT)

    assert result.ok is True
    assert "## 프로젝트" in (result.artifact.body_markdown if result.artifact else "")


async def test_portfolio_job_uses_200_second_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, float] = {}

    async def fake_wait_for(coro, timeout):  # noqa: ANN001
        seen["timeout"] = timeout
        return await coro

    monkeypatch.delenv("JOB_TIMEOUT", raising=False)
    monkeypatch.setattr("app.api.jobs.asyncio.wait_for", fake_wait_for)
    job = JobRequest(
        job_id="j1",
        user_id="u1",
        job_type="portfolio",
        repos=[{"owner": "acme", "name": "demo"}],
        document=DocumentSpec(kind="portfolio", profile_fields=FIELDS),
    )
    fake = FakeGitHub()
    real = __import__("app.collect.github", fromlist=["collect_github"]).collect_github

    async def collect(req, github_pat, **_kwargs):
        return await real(req, github_pat, call_tool=fake)

    monkeypatch.setattr("app.api.jobs.collect_github", collect)
    result = await handle_job(job, PAT)
    assert result.ok is True
    assert seen["timeout"] == 200


async def test_portfolio_job_joins_til_when_notion_is_connected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import date

    async def til_collect(parent_id, token, **_kwargs):
        return NotionSnapshot(
            entries=[
                TilEntry(
                    date=date(2026, 8, 20),
                    title="캐시 개선",
                    body_markdown="작업 저장소: https://github.com/acme/demo",
                    page_id="til-1",
                )
            ],
            complete=True,
        )

    monkeypatch.setattr("app.api.jobs.collect_notion_til", til_collect)
    fake = FakeGitHub()
    real = __import__("app.collect.github", fromlist=["collect_github"]).collect_github

    async def collect(req, github_pat, **_kwargs):
        return await real(req, github_pat, call_tool=fake)

    monkeypatch.setattr("app.api.jobs.collect_github", collect)
    job = JobRequest(
        job_id="j1",
        user_id="u1",
        job_type="portfolio",
        repos=[{"owner": "acme", "name": "demo"}],
        notion={"parent_id": "dashboard"},
        document=DocumentSpec(kind="portfolio", profile_fields=FIELDS),
    )
    result = await handle_job(job, PAT, NOTION_TOKEN)
    body = result.artifact.body_markdown if result.artifact else ""
    assert result.ok is True
    assert "캐시 개선" in body
    assert "**배운 것**" in body
    assert "publish_briefs" not in result.proposal.model_dump()
