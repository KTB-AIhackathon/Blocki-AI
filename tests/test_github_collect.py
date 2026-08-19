import inspect
from datetime import datetime, timezone

import pytest

from app.collect.github import collect_github
from app.contracts import (
    CollectRequest,
    GitHubCollectError,
    RepoCursor,
    RepoRef,
    snapshot_digest_of,
)

PAT = "gho_test_token_must_not_leak"
SHA = "abc123def456"


def _req(**kwargs) -> CollectRequest:
    data = {
        "job_id": "job-1",
        "repos": [RepoRef(owner="acme", name="demo")],
        "needs": ["activity"],
    }
    data.update(kwargs)
    return CollectRequest(**data)


def _meta(**kwargs) -> dict:
    body = {
        "default_branch": "main",
        "head_sha": SHA,
        "description": "demo repo",
        "topics": ["python"],
        "languages": [{"name": "Python", "bytes": 10}],
        "manifest_files": ["pyproject.toml"],
    }
    body.update(kwargs)
    return body


class Fake:
    def __init__(self, handlers: dict) -> None:
        self.handlers = handlers
        self.calls: list[tuple[str, dict]] = []

    async def __call__(self, name: str, args: dict):
        self.calls.append((name, args))
        handler = self.handlers.get(name)
        if handler is None:
            raise AssertionError(name)
        if isinstance(handler, BaseException):
            raise handler
        if callable(handler):
            result = handler(args)
            if inspect.isawaitable(result):
                return await result
            return result
        return handler


async def test_get_me_and_one_repo_commits():
    fake = Fake(
        {
            "get_me": {"login": "alice"},
            "get_repo_meta": _meta(),
            "list_commits": [
                {
                    "sha": SHA,
                    "message": "init",
                    "author": "alice",
                    "committed_at": "2026-01-01T00:00:00Z",
                }
            ],
            "list_issues": [],
            "list_pull_requests": [],
        }
    )
    snap = await collect_github(_req(), PAT, call_tool=fake)
    assert snap.viewer_login == "alice"
    assert snap.complete is True
    assert len(snap.repos) == 1
    repo = snap.repos[0]
    assert repo.owner == "acme" and repo.name == "demo"
    assert repo.head_sha == SHA
    assert len(repo.commits) == 1
    assert repo.commits[0].message == "init"
    assert repo.commits[0].author == "alice"
    assert snap.next_cursor[0].head_sha == SHA
    assert snap.snapshot_digest == snapshot_digest_of(snap.repos, snap.viewer_login)
    dumped = snap.model_dump_json()
    assert PAT not in dumped
    assert not any(n == "list_repos" for n, _ in fake.calls)


async def test_complete_false_on_one_repo_failure():
    def meta(args):
        if args["name"] == "bad":
            raise RuntimeError("repo missing")
        return _meta()

    fake = Fake(
        {
            "get_me": {"login": "alice"},
            "get_repo_meta": meta,
            "list_commits": [],
            "list_issues": [],
            "list_pull_requests": [],
        }
    )
    req = _req(
        repos=[
            RepoRef(owner="acme", name="demo"),
            RepoRef(owner="acme", name="bad"),
        ]
    )
    snap = await collect_github(req, PAT, call_tool=fake)
    assert snap.complete is False
    assert [r.name for r in snap.repos] == ["demo"]
    assert snap.warnings
    assert any("bad" in w for w in snap.warnings)
    assert PAT not in "".join(snap.warnings)


async def test_429_maps_to_github_rate_limit():
    fake = Fake({"get_me": RuntimeError("429 rate limit")})
    with pytest.raises(GitHubCollectError) as caught:
        await collect_github(_req(), PAT, call_tool=fake)
    err = caught.value.error
    assert err.code == "github_rate_limit"
    assert err.retryable is True
    assert PAT not in err.message
    assert fake.calls.count(("get_me", {})) == 3


async def test_429_retries_then_succeeds():
    hits = {"n": 0}

    async def get_me(_args):
        hits["n"] += 1
        if hits["n"] < 3:
            raise RuntimeError("429 Too Many Requests")
        return {"login": "alice"}

    fake = Fake(
        {
            "get_me": get_me,
            "get_repo_meta": _meta(),
            "list_commits": [],
            "list_issues": [],
            "list_pull_requests": [],
        }
    )
    snap = await collect_github(_req(), PAT, call_tool=fake)
    assert snap.viewer_login == "alice"
    assert hits["n"] == 3


async def test_401_maps_to_github_auth():
    fake = Fake({"get_me": RuntimeError("401 Unauthorized")})
    with pytest.raises(GitHubCollectError) as caught:
        await collect_github(_req(), PAT, call_tool=fake)
    err = caught.value.error
    assert err.code == "github_auth"
    assert err.retryable is False
    assert PAT not in err.message
    assert PAT not in str(caught.value)


async def test_cursor_same_sha_empty_activity():
    fake = Fake(
        {
            "get_me": {"login": "alice"},
            "get_repo_meta": _meta(head_sha=SHA),
            "list_commits": [
                {
                    "sha": SHA,
                    "message": "should be ignored",
                    "author": "alice",
                    "committed_at": "2026-01-01T00:00:00Z",
                }
            ],
            "list_issues": [{"number": 1, "title": "x", "state": "open"}],
            "list_pull_requests": [{"number": 2, "title": "y", "state": "open"}],
        }
    )
    req = _req(
        cursor=[
            RepoCursor(
                owner="acme",
                name="demo",
                head_sha=SHA,
                last_success_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        ]
    )
    snap = await collect_github(req, PAT, call_tool=fake)
    repo = snap.repos[0]
    assert repo.head_sha == SHA
    assert repo.commits == []
    assert len(repo.issues) == 1
    assert len(repo.pull_requests) == 1
    called = {name for name, _ in fake.calls}
    assert "list_commits" not in called
    assert "list_issues" in called
    assert "list_pull_requests" in called
    assert snap.next_cursor[0].head_sha == SHA
    assert PAT not in snap.model_dump_json()


async def test_readme_404_keeps_repo():
    fake = Fake(
        {
            "get_me": {"login": "alice"},
            "get_repo_meta": _meta(),
            "get_file": RuntimeError("404 Not Found"),
        }
    )
    snap = await collect_github(
        _req(needs=["readme"]),
        PAT,
        call_tool=fake,
    )
    assert snap.complete is True
    assert len(snap.repos) == 1
    assert snap.repos[0].readme is None
    assert snap.next_cursor[0].head_sha == SHA


async def test_readme_path_forwarded_to_get_file():
    fake = Fake(
        {
            "get_me": {"login": "alice"},
            "get_repo_meta": _meta(),
            "get_file": {
                "path": "docs/README.md",
                "blob_sha": "fff",
                "content": "# hi",
            },
        }
    )
    snap = await collect_github(
        _req(needs=["readme"], readme_path="docs/README.md"),
        PAT,
        call_tool=fake,
    )
    assert snap.repos[0].readme is not None
    assert snap.repos[0].readme.path == "docs/README.md"
    get_file_calls = [args for name, args in fake.calls if name == "get_file"]
    assert get_file_calls and get_file_calls[0]["path"] == "docs/README.md"


def test_jsonish_parses_mcp_text_blocks():
    from app.collect.github import _jsonish

    raw = [{"type": "text", "text": '[{"login":"alice"}]'}]
    assert _jsonish(raw) == [{"login": "alice"}]


def test_jsonish_parses_split_json_objects():
    from app.collect.github import _jsonish

    raw = [
        {"type": "text", "text": '{"sha":"aaa","message":"one"}'},
        {"type": "text", "text": '{"sha":"bbb","message":"two"}'},
    ]
    assert _jsonish(raw) == [
        {"sha": "aaa", "message": "one"},
        {"sha": "bbb", "message": "two"},
    ]


def test_as_list_wraps_single_commit_object():
    from app.collect.github import _as_list, _commits

    raw = {"sha": "aaa", "message": "one"}
    assert _as_list(raw) == [raw]
    assert _commits(raw)[0].sha == "aaa"
