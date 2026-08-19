from __future__ import annotations

import sys
import types

import pytest
from fastapi.testclient import TestClient

from app.contracts import (
    ArtifactProposal,
    ExecuteResult,
    GitHubCollectError,
    GitHubSnapshot,
    JobError,
    RepoCursor,
    utcnow,
)


def _install_peer_stubs() -> None:
    async def _must_mock(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("peer function must be monkeypatched")

    stubs = (
        ("app.collect.github", "collect_github"),
        ("app.artifacts", "build_artifact"),
        ("app.execute.readme_pr", "execute_readme_pr"),
    )
    for mod_name, func_name in stubs:
        mod = sys.modules.get(mod_name)
        if mod is None:
            try:
                mod = __import__(mod_name, fromlist=[func_name])
            except ModuleNotFoundError:
                mod = types.ModuleType(mod_name)
                sys.modules[mod_name] = mod
                parent_name, _, child = mod_name.rpartition(".")
                if parent_name:
                    parent = sys.modules.get(parent_name)
                    if parent is None:
                        try:
                            parent = __import__(parent_name, fromlist=[child])
                        except ImportError:
                            parent = None
                    if parent is not None:
                        setattr(parent, child, mod)
        if not hasattr(mod, func_name):
            setattr(mod, func_name, _must_mock)


_install_peer_stubs()

from app.main import create_app  # noqa: E402


def test_health_ok() -> None:
    client = TestClient(create_app())
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}

KEY = "dev-internal-key"
PAT = "ghs_test_pat_do_not_leak"

PROGRESS_JOB = {
    "job_id": "job-progress-1",
    "user_id": "user-1",
    "job_type": "progress_summary",
}

PROFILE_JOB = {
    "job_id": "job-profile-1",
    "user_id": "user-1",
    "job_type": "profile_document",
}

README_JOB = {
    "job_id": "job-readme-1",
    "user_id": "user-1",
    "job_type": "readme_proposal",
    "readme": {"owner": "octo", "repo": "blocki", "path": "README.md"},
}

EXECUTE_BODY = {
    "execution_id": "exec-1",
    "proposal_id": "prop-1",
    "action_digest": "a" * 64,
    "idempotency_key": "prop-1",
    "action": {
        "type": "create_readme_pr",
        "owner": "octo",
        "repo": "blocki",
        "path": "README.md",
        "base_branch": "main",
        "expected_base_sha": "b" * 40,
        "expected_blob_sha": "c" * 40,
        "replacement_markdown": "# hi",
        "pr_title": "docs: readme",
        "pr_body": "update",
    },
}


def _headers(*, key: str | None = KEY, pat: str | None = PAT) -> dict[str, str]:
    headers: dict[str, str] = {}
    if key is not None:
        headers["X-Internal-Key"] = key
    if pat is not None:
        headers["X-GitHub-Pat"] = pat
    return headers


def _snapshot() -> GitHubSnapshot:
    return GitHubSnapshot(
        collected_at=utcnow(),
        complete=True,
        snapshot_digest="d" * 64,
        viewer_login="octocat",
        next_cursor=[
            RepoCursor(
                owner="octo",
                name="blocki",
                head_sha="a" * 40,
                last_success_at=utcnow(),
            )
        ],
    )


def _proposal(**overrides: object) -> ArtifactProposal:
    payload: dict[str, object] = {
        "proposal_id": "prop-1",
        "job_id": PROGRESS_JOB["job_id"],
        "status": "proposed",
        "kind": "progress",
        "body_markdown": "memo",
    }
    payload.update(overrides)
    return ArtifactProposal(**payload)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("INTERNAL_API_KEY", KEY)
    return TestClient(create_app())


def test_jobs_missing_internal_key_returns_401(client: TestClient) -> None:
    response = client.post("/internal/jobs", json=PROGRESS_JOB, headers=_headers(key=None))
    assert response.status_code == 401
    assert PAT not in response.text


def test_jobs_wrong_internal_key_returns_401(client: TestClient) -> None:
    response = client.post(
        "/internal/jobs",
        json=PROGRESS_JOB,
        headers=_headers(key="wrong-key"),
    )
    assert response.status_code == 401
    assert PAT not in response.text


def test_jobs_missing_pat_returns_missing_pat(client: TestClient) -> None:
    response = client.post("/internal/jobs", json=PROGRESS_JOB, headers=_headers(pat=None))
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["job_id"] == PROGRESS_JOB["job_id"]
    assert body["error"]["code"] == "missing_pat"
    assert body["proposal"] is None
    assert PAT not in response.text


def test_jobs_blank_pat_returns_missing_pat(client: TestClient) -> None:
    response = client.post("/internal/jobs", json=PROGRESS_JOB, headers=_headers(pat="   "))
    assert response.status_code == 200
    assert response.json()["error"]["code"] == "missing_pat"


def test_jobs_invalid_job_type_returns_422(client: TestClient) -> None:
    payload = {**PROGRESS_JOB, "job_type": "not_a_job"}
    response = client.post("/internal/jobs", json=payload, headers=_headers())
    assert response.status_code == 422


def test_jobs_profile_without_document_returns_422(client: TestClient) -> None:
    response = client.post("/internal/jobs", json=PROFILE_JOB, headers=_headers())
    assert response.status_code == 422


def test_jobs_readme_without_readme_returns_422(client: TestClient) -> None:
    payload = {"job_id": "job-readme-1", "user_id": "user-1", "job_type": "readme_proposal"}
    response = client.post("/internal/jobs", json=payload, headers=_headers())
    assert response.status_code == 422


def test_jobs_invalid_readme_path_returns_422(client: TestClient) -> None:
    payload = {
        **README_JOB,
        "readme": {"owner": "octo", "repo": "blocki", "path": "../secrets.md"},
    }
    response = client.post("/internal/jobs", json=payload, headers=_headers())
    assert response.status_code == 422


def test_jobs_collect_then_build_ok(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, object] = {}

    async def fake_collect(req: object, github_pat: str) -> GitHubSnapshot:
        seen["needs"] = list(req.needs)
        seen["pat"] = github_pat
        seen["dump"] = req.model_dump()
        return _snapshot()

    async def fake_build(snapshot: GitHubSnapshot, job: object, llm: object = None) -> ArtifactProposal:
        seen["llm"] = llm
        seen["job_id"] = job.job_id
        return _proposal()

    monkeypatch.setattr("app.api.jobs.collect_github", fake_collect)
    monkeypatch.setattr("app.api.jobs.build_artifact", fake_build)

    response = client.post("/internal/jobs", json=PROGRESS_JOB, headers=_headers())
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["error"] is None
    assert body["proposal"]["status"] == "proposed"
    assert body["proposal"]["proposal_digest"]
    assert body["snapshot_summary"]["complete"] is True
    assert body["next_cursor"][0]["name"] == "blocki"
    assert seen["needs"] == ["activity"]
    assert seen["pat"] == PAT
    assert "github_pat" not in seen["dump"]
    assert seen["llm"] is None
    assert PAT not in response.text


def test_jobs_no_change_is_ok_true(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_collect(req: object, github_pat: str) -> GitHubSnapshot:
        return _snapshot()

    async def fake_build(*_args: object, **_kwargs: object) -> ArtifactProposal:
        return _proposal(status="no_change", body_markdown="")

    monkeypatch.setattr("app.api.jobs.collect_github", fake_collect)
    monkeypatch.setattr("app.api.jobs.build_artifact", fake_build)

    body = client.post("/internal/jobs", json=PROGRESS_JOB, headers=_headers()).json()
    assert body["ok"] is True
    assert body["proposal"]["status"] == "no_change"


def test_jobs_partial_is_ok_true(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_collect(req: object, github_pat: str) -> GitHubSnapshot:
        return _snapshot()

    async def fake_build(*_args: object, **_kwargs: object) -> ArtifactProposal:
        return _proposal(status="partial")

    monkeypatch.setattr("app.api.jobs.collect_github", fake_collect)
    monkeypatch.setattr("app.api.jobs.build_artifact", fake_build)

    assert client.post("/internal/jobs", json=PROGRESS_JOB, headers=_headers()).json()["ok"] is True


def test_jobs_blocked_is_ok_false(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_collect(req: object, github_pat: str) -> GitHubSnapshot:
        return _snapshot()

    async def fake_build(*_args: object, **_kwargs: object) -> ArtifactProposal:
        return _proposal(
            status="blocked",
            kind="resume",
            error=JobError(code="blocked", message="name required"),
        )

    monkeypatch.setattr("app.api.jobs.collect_github", fake_collect)
    monkeypatch.setattr("app.api.jobs.build_artifact", fake_build)

    body = client.post("/internal/jobs", json=PROGRESS_JOB, headers=_headers()).json()
    assert body["ok"] is False
    assert body["error"]["code"] == "blocked"


def test_jobs_github_collect_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_collect(req: object, github_pat: str) -> GitHubSnapshot:
        raise GitHubCollectError(
            JobError(code="github_auth", message="unauthorized", retryable=False)
        )

    monkeypatch.setattr("app.api.jobs.collect_github", fake_collect)

    response = client.post("/internal/jobs", json=PROGRESS_JOB, headers=_headers())
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "github_auth"
    assert body["proposal"] is None
    assert PAT not in response.text


def test_executions_missing_internal_key_returns_401(client: TestClient) -> None:
    response = client.post("/internal/executions", json=EXECUTE_BODY, headers=_headers(key=None))
    assert response.status_code == 401


def test_executions_missing_pat_returns_rejected(client: TestClient) -> None:
    response = client.post(
        "/internal/executions", json=EXECUTE_BODY, headers=_headers(pat=None)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "rejected"
    assert body["error"]["code"] == "missing_pat"
    assert body["execution_id"] == "exec-1"
    assert PAT not in response.text


def test_executions_calls_execute_readme_pr(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, object] = {}

    async def fake_execute(req: object, github_pat: str) -> ExecuteResult:
        seen["pat"] = github_pat
        seen["execution_id"] = req.execution_id
        return ExecuteResult(
            execution_id=req.execution_id,
            status="created",
            pr_url="https://github.com/octo/blocki/pull/1",
        )

    monkeypatch.setattr("app.api.executions.execute_readme_pr", fake_execute)

    response = client.post("/internal/executions", json=EXECUTE_BODY, headers=_headers())
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "created"
    assert body["pr_url"].endswith("/pull/1")
    assert seen["pat"] == PAT
    assert seen["execution_id"] == "exec-1"
    assert PAT not in response.text
