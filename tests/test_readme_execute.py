from __future__ import annotations

import logging
from typing import Any

import pytest

from app.api.executions import post_execution
from app.contracts import (
    ExecuteRequest,
    ReadmePrAction,
    action_digest_of,
    is_allowed_readme_path,
)
from app.execute import execute_readme_pr

PROPOSAL_ID = "11111111-2222-4333-8444-555555555555"
EXECUTION_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
HEAD_BRANCH = f"blocki/readme-{PROPOSAL_ID}"
BASE_SHA = "a" * 40
BLOB_SHA = "b" * 40
PR_URL = "https://github.com/acme/demo/pull/7"
REPLACEMENT = "# New README\n"


class FakeGitHub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.prs: list[dict[str, Any]] = []
        self.branches: dict[str, str] = {}
        self.files: dict[tuple[str, str], dict[str, str]] = {
            ("README.md", "main"): {"blob_sha": BLOB_SHA, "content": "# Old README\n"}
        }
        self.refs: dict[str, str] = {"main": BASE_SHA}

    async def __call__(self, name: str, args: dict[str, Any]) -> Any:
        self.calls.append((name, args))
        if name == "list_prs":
            return list(self.prs)
        if name == "get_branch":
            sha = self.branches.get(args["branch"])
            return {"sha": sha} if sha else None
        if name == "get_file":
            return self.files.get((args["path"], args["ref"]))
        if name == "get_ref":
            sha = self.refs.get(args["ref"])
            return {"sha": sha} if sha else None
        if name == "create_branch":
            self.branches[args["branch"]] = args["from_sha"]
            return None
        if name == "update_file":
            self.files[(args["path"], args["branch"])] = {
                "blob_sha": "c" * 40,
                "content": args["content"],
            }
            return None
        if name == "create_pr":
            pr = {"url": PR_URL, "state": "open", "number": 7}
            self.prs.append(pr)
            return {"url": PR_URL}
        raise AssertionError(f"unexpected tool {name}")

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


def _action(**overrides: Any) -> ReadmePrAction:
    data: dict[str, Any] = {
        "type": "create_readme_pr",
        "owner": "acme",
        "repo": "demo",
        "path": "README.md",
        "base_branch": "main",
        "expected_base_sha": BASE_SHA,
        "expected_blob_sha": BLOB_SHA,
        "replacement_markdown": REPLACEMENT,
        "pr_title": "docs: README 업데이트",
        "pr_body": "Blocki README PR",
    }
    data.update(overrides)
    if not is_allowed_readme_path(data["path"]):
        return ReadmePrAction.model_construct(**data)
    return ReadmePrAction(**data)


def _request(action: ReadmePrAction, *, digest: str | None = None, idempotency: str | None = None) -> ExecuteRequest:
    return ExecuteRequest(
        execution_id=EXECUTION_ID,
        proposal_id=PROPOSAL_ID,
        action_digest=digest if digest is not None else action_digest_of(action),
        action=action,
        idempotency_key=idempotency if idempotency is not None else PROPOSAL_ID,
    )


async def test_digest_mismatch_rejected() -> None:
    github = FakeGitHub()
    result = await execute_readme_pr(_request(_action(), digest="0" * 64), "secret-pat", call_tool=github)

    assert result.status == "rejected"
    assert result.error is not None
    assert result.error.code == "validation"
    assert result.pr_url is None
    assert github.calls == []


async def test_path_not_allowed_rejected() -> None:
    github = FakeGitHub()
    action = _action(path="src/secret.py")
    result = await execute_readme_pr(_request(action), "secret-pat", call_tool=github)

    assert result.status == "rejected"
    assert result.error is not None
    assert result.error.code == "validation"
    assert github.calls == []


async def test_existing_pr_duplicate() -> None:
    github = FakeGitHub()
    github.prs = [{"url": PR_URL, "state": "closed", "number": 7}]
    result = await execute_readme_pr(_request(_action()), "secret-pat", call_tool=github)

    assert result.status == "duplicate"
    assert result.pr_url == PR_URL
    assert result.error is None
    assert github.names() == ["list_prs"]
    assert github.calls[0][1]["head_branch"] == HEAD_BRANCH
    assert "create_pr" not in github.names()
    assert "update_file" not in github.names()


async def test_stale_sha_rejected() -> None:
    github = FakeGitHub()
    github.refs["main"] = "d" * 40
    result = await execute_readme_pr(_request(_action()), "secret-pat", call_tool=github)

    assert result.status == "rejected"
    assert result.error is not None
    assert result.error.code == "stale_sha"
    assert result.pr_url is None
    assert "create_branch" not in github.names()
    assert "create_pr" not in github.names()


async def test_429_rejected_after_retries() -> None:
    class Boom:
        def __init__(self) -> None:
            self.n = 0

        async def __call__(self, name: str, args: dict[str, Any]) -> Any:
            self.n += 1
            raise RuntimeError("429 rate limit")

    boom = Boom()
    result = await execute_readme_pr(_request(_action()), "secret-pat", call_tool=boom)
    assert result.status == "rejected"
    assert result.error is not None
    assert result.error.code == "github_rate_limit"
    assert result.error.retryable is True
    assert boom.n == 3


async def test_happy_path_created() -> None:
    github = FakeGitHub()
    result = await execute_readme_pr(_request(_action()), "secret-pat", call_tool=github)

    assert result.status == "created"
    assert result.pr_url == PR_URL
    assert result.error is None
    assert github.names() == [
        "list_prs",
        "get_branch",
        "get_ref",
        "get_file",
        "create_branch",
        "update_file",
        "create_pr",
    ]
    created = dict(github.calls)["create_branch"]
    assert created["branch"] == HEAD_BRANCH
    assert created["from_sha"] == BASE_SHA
    updated = dict(github.calls)["update_file"]
    assert updated["branch"] == HEAD_BRANCH
    assert updated["branch"] != "main"
    assert updated["expected_blob_sha"] == BLOB_SHA
    assert updated["content"] == REPLACEMENT
    opened = dict(github.calls)["create_pr"]
    assert opened["head"] == HEAD_BRANCH
    assert opened["base"] == "main"
    assert "secret-pat" not in str(github.calls)


async def test_execution_exception_is_logged_without_the_token(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    token = "secret-pat"

    async def boom(*_args, **_kwargs):
        raise RuntimeError(f"failed with {token}")

    monkeypatch.setattr("app.api.executions.execute_readme_pr", boom)
    with caplog.at_level(logging.ERROR, logger="app.api.executions"):
        result = await post_execution(_request(_action()), token, None)

    assert result.status == "rejected"
    assert result.error is not None
    assert result.error.code == "internal"
    assert EXECUTION_ID in caplog.text
    assert "RuntimeError" in caplog.text
    assert "«redacted»" in caplog.text
    assert token not in caplog.text
