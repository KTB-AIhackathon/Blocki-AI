"""Shared GitHub MCP double.

`FakeGitHub` answers the logical tool names `app/collect/mcp.py` exposes, so a
test never has to know real MCP tool names.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

PAT = "gho_test_token_must_not_leak"
NOTION_TOKEN = "ntn_test_token_must_not_leak"
SHA = "abc123def456"
NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def days_ago(days: int) -> str:
    return (NOW - timedelta(days=days)).isoformat().replace("+00:00", "Z")


def commit(sha: str, message: str, author: str = "alice", days: int = 1) -> dict[str, Any]:
    return {
        "sha": sha,
        "message": message,
        "author": author,
        "author_email": f"{author}@example.com",
        "committed_at": days_ago(days),
    }


def repo_meta(**overrides: Any) -> dict[str, Any]:
    meta = {
        "default_branch": "main",
        "head_sha": SHA,
        "description": "demo repo",
        "html_url": "https://github.com/acme/demo",
        "topics": ["python", "fastapi", "hackathon"],
        "languages": [{"name": "Python", "bytes": 9000}, {"name": "HTML", "bytes": 100}],
        "manifest_files": ["pyproject.toml", "Dockerfile"],
        "fork": False,
        "archived": False,
        "stars": 3,
        "pushed_at": days_ago(1),
    }
    meta.update(overrides)
    return meta


class FakeGitHub:
    """Records every logical call so tests can assert on collect policy."""

    def __init__(self, **handlers: Any) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.prs: list[dict[str, Any]] = []
        self.branches: dict[str, str] = {}
        self.files: dict[tuple[str, str], dict[str, Any]] = {
            ("README.md", "main"): {
                "path": "README.md",
                "blob_sha": "blob1",
                "content": "# old\n",
            }
        }
        self.refs = {"main": SHA}
        # An explicit handler always wins over the simulated repository, so a
        # test can make a single tool fail without rebuilding the whole double.
        self.overrides = set(handlers)
        self.handlers: dict[str, Any] = {
            "get_me": {"login": "alice", "email": "alice@example.com"},
            "list_repos": [{"full_name": "acme/demo"}],
            "get_repo_meta": repo_meta(),
            "list_commits": [commit(SHA, "feat: init")],
            "list_issues": [],
            "list_pull_requests": [],
        }
        self.handlers.update(handlers)

    def args_for(self, name: str) -> list[dict[str, Any]]:
        return [args for called, args in self.calls if called == name]

    def called(self, name: str) -> bool:
        return any(called == name for called, _ in self.calls)

    async def __call__(self, name: str, args: dict[str, Any]) -> Any:
        self.calls.append((name, args))
        if name not in self.overrides:
            simulated = await self._write(name, args)
            if simulated is not _MISS:
                return simulated
        if name not in self.handlers:
            raise AssertionError(f"unexpected tool: {name}")
        handler = self.handlers[name]
        if isinstance(handler, BaseException):
            raise handler
        if callable(handler):
            result = handler(args)
            return await result if inspect.isawaitable(result) else result
        return handler

    async def _write(self, name: str, args: dict[str, Any]) -> Any:
        if name == "get_file":
            path = args.get("path") or "README.md"
            ref = args.get("ref")
            if ref is None:
                return self.files.get((path, "main"))
            return self.files.get((path, ref))
        if name == "list_prs":
            return list(self.prs)
        if name == "get_branch":
            sha = self.branches.get(args["branch"])
            return {"sha": sha} if sha else None
        if name == "get_ref":
            return {"sha": self.refs[args["ref"]]}
        if name == "create_branch":
            self.branches[args["branch"]] = args["from_sha"]
            return None
        if name == "update_file":
            self.files[(args["path"], args["branch"])] = {
                "blob_sha": "newblob",
                "content": args["content"],
            }
            return None
        if name == "create_pr":
            url = f"https://github.com/acme/demo/pull/{len(self.prs) + 1}"
            self.prs.append({"url": url, "state": "open", "number": len(self.prs) + 1})
            return {"url": url}
        return _MISS


class _Miss:
    pass


_MISS = _Miss()


@pytest.fixture
def fake_github() -> FakeGitHub:
    return FakeGitHub()


@pytest.fixture(autouse=True)
def _no_live_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests assert on deterministic rendering; a real provider would drift."""
    monkeypatch.setenv("BLOCKI_LLM_PROVIDER", "none")
    from app.llm import client

    client.reset()
    yield
    client.reset()
