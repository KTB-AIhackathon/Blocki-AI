"""The response shape Spring's `DocumentGenerationClient.validate` will accept.

Transcribed from Blocki-Backend `ai/client/DocumentGenerationClient.java`. That
validator rejects the whole response on any mismatch and the job then fails as
`AI_PIPELINE_FAILED`, which reads like a pipeline bug rather than a wire
mismatch — so the rules live here as executable text instead of a comment.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import PAT, FakeGitHub

KEY = "dev-internal-key"
HEADERS = {"X-Internal-Key": KEY, "X-GitHub-Pat": PAT}

#: `Set.of("proposed", "partial", "no_change")` in validate().
SUCCESS_STATUSES = {"proposed", "partial", "no_change"}
#: The only source name the validator will not throw on.
KNOWN_SOURCE = "GITHUB"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("INTERNAL_API_KEY", KEY)
    return TestClient(create_app())


@pytest.fixture
def stub_github(monkeypatch: pytest.MonkeyPatch) -> FakeGitHub:
    fake = FakeGitHub()
    real = __import__("app.collect.github", fromlist=["collect_github"]).collect_github

    async def collect(req, github_pat, **kwargs):
        return await real(req, github_pat, call_tool=fake)

    monkeypatch.setattr("app.api.jobs.collect_github", collect)
    return fake


def spring_request(kind: str) -> dict:
    """Exactly what DocumentGenerationClient.generate posts. No repos, no since."""
    return {
        "job_id": "11111111-1111-1111-1111-111111111111",
        "user_id": "22222222-2222-2222-2222-222222222222",
        "job_type": "profile_document",
        "document": {
            "kind": kind,
            "profile_fields": {
                "name": "홍길동",
                "contact_md": "",
                "experience_md": "",
                "education_md": "",
            },
        },
    }


def validate(body: dict) -> str:
    """Java's validate(), line for line. Raises what it would throw."""
    if body.get("ok") is None:
        raise AssertionError("AI response must include ok")
    missing = body.get("missing_sources") or []
    if not all(source == KNOWN_SOURCE for source in missing):
        raise AssertionError(f"AI response has unsupported missing source: {missing}")
    if not body["ok"]:
        if body.get("status") != "failed" or not (body.get("error_code") or "").strip():
            raise AssertionError(f"AI failure response is invalid: {body.get('status')}")
        return "failed"
    artifact = body.get("artifact") or {}
    if body.get("status") not in SUCCESS_STATUSES or not all(
        (artifact.get(field) or "").strip() for field in ("kind", "title", "body_markdown")
    ):
        raise AssertionError(f"AI success response is invalid: {body.get('status')}")
    return body["status"]


@pytest.mark.parametrize("kind", ["portfolio", "resume"])
def test_spring_accepts_a_generated_document(client, stub_github, kind) -> None:
    body = client.post("/internal/jobs", json=spring_request(kind), headers=HEADERS).json()

    assert validate(body) in SUCCESS_STATUSES
    # 이름은 Notion 페이지 제목이 된다. 본문은 첫 섹션부터 시작한다.
    assert body["artifact"]["title"].startswith("홍길동")
    body_md = body["artifact"]["body_markdown"].lstrip()
    assert body_md.startswith("##") or body_md.startswith(">")
    assert not any(
        line.startswith("# ") for line in body["artifact"]["body_markdown"].splitlines()
    )


def test_a_document_grounded_in_repositories_reports_no_missing_source(
    client, stub_github
) -> None:
    body = client.post("/internal/jobs", json=spring_request("portfolio"), headers=HEADERS).json()

    assert body["missing_sources"] == []
    assert body["snapshot_summary"]["repo_count"] > 0


def test_a_resume_with_blank_career_is_partial_but_not_a_missing_source(
    client, stub_github
) -> None:
    """Career blanks are the user's to fill. Calling that a GitHub gap misleads."""
    body = client.post("/internal/jobs", json=spring_request("resume"), headers=HEADERS).json()

    assert validate(body) == "partial"
    assert body["missing_sources"] == []
    assert "experience_md" in body["proposal"]["unresolved_fields"]


def test_a_missing_pat_is_a_failure_spring_can_read(client) -> None:
    body = client.post(
        "/internal/jobs", json=spring_request("portfolio"), headers={"X-Internal-Key": KEY}
    ).json()

    assert validate(body) == "failed"
    assert body["error_code"] == "missing_pat"
    assert body["missing_sources"] == [KNOWN_SOURCE]
    assert body["error"]["retryable"] is False


def test_spring_failure_response_keeps_existing_error_retryable() -> None:
    from app.contracts import JobError

    dumped = JobError(code="internal", message="job timed out", retryable=True).model_dump()
    assert dumped["retryable"] is True
    assert dumped["code"] == "internal"


def test_a_document_job_that_succeeds_always_carries_an_artifact(client, stub_github) -> None:
    """Spring reads `artifact.body_markdown` and fails the job without it."""
    for kind in ("portfolio", "resume"):
        body = client.post("/internal/jobs", json=spring_request(kind), headers=HEADERS).json()
        assert body["ok"] is True
        assert body["artifact"] is not None, kind
        assert body["artifact"]["title"] == ("홍길동 포트폴리오" if kind == "portfolio" else "홍길동 이력서")


def test_the_worker_still_answers_its_own_richer_shape(client, stub_github) -> None:
    """The Spring fields are additions. Test-spring's client reads these instead."""
    body = client.post("/internal/jobs", json=spring_request("portfolio"), headers=HEADERS).json()

    assert body["proposal"]["status"] == body["status"]
    assert body["notion"]["skipped_reason"] == "missing_token"
    assert body["job_id"] == spring_request("portfolio")["job_id"]
