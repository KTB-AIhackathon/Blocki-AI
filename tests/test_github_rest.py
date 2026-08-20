from __future__ import annotations

import httpx
import pytest

from app.collect.github import collect_github
from app.collect.github_rest import GithubRestError, open_rest_session
from app.collect.mcp import open_read_session
from app.collect.parse import http_status
from app.contracts import CollectPolicy, CollectRequest, RepoRef
from tests.conftest import SHA


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url="https://api.github.com",
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_rest_get_me_and_list_repos() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user":
            return httpx.Response(200, json={"login": "alice", "email": "a@b.c"})
        if request.url.path == "/user/repos":
            return httpx.Response(
                200,
                json=[{"full_name": "alice/demo", "owner": {"login": "alice"}, "name": "demo"}],
            )
        return httpx.Response(404, json={"message": "not found"})

    call = await open_rest_session("gho_oauth", client=_client(handler))
    me = await call("get_me", {})
    repos = await call("list_repos", {"limit": 5})
    assert me == {"login": "alice", "email": "a@b.c"}
    assert repos[0]["full_name"] == "alice/demo"


@pytest.mark.asyncio
async def test_rest_401_is_github_rest_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Bad credentials"})

    call = await open_rest_session("gho_bad", client=_client(handler))
    with pytest.raises(GithubRestError) as caught:
        await call("get_me", {})
    assert caught.value.status_code == 401
    assert http_status(caught.value, str(caught.value)) == 401


@pytest.mark.asyncio
async def test_open_read_session_does_not_open_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    async def fake_rest(token: str, *, client=None):
        seen["token"] = token

        async def call(name: str, args: dict) -> object:
            return {"login": "alice"}

        return call

    def boom(*_a, **_k):
        raise AssertionError("Copilot MCP must not be opened for collect")

    monkeypatch.setattr("app.collect.github_rest.open_rest_session", fake_rest)
    monkeypatch.setattr(
        "langchain_mcp_adapters.client.MultiServerMCPClient", boom, raising=False
    )
    tool = await open_read_session("gho_from_spring")
    assert seen["token"] == "gho_from_spring"
    assert await tool("get_me", {}) == {"login": "alice"}


@pytest.mark.asyncio
async def test_collect_reads_rest_repo_payloads() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/user":
            return httpx.Response(200, json={"login": "alice", "email": "a@b.c"})
        if path == "/repos/acme/demo":
            return httpx.Response(
                200,
                json={
                    "default_branch": "main",
                    "description": "demo",
                    "html_url": "https://github.com/acme/demo",
                    "topics": ["python"],
                    "fork": False,
                    "archived": False,
                    "stargazers_count": 3,
                    "pushed_at": "2026-06-01T00:00:00Z",
                },
            )
        if path.endswith("/languages"):
            return httpx.Response(200, json={"Python": 9000})
        if path.endswith("/commits"):
            return httpx.Response(
                200,
                json=[
                    {
                        "sha": SHA,
                        "commit": {
                            "message": "feat: init",
                            "author": {
                                "name": "alice",
                                "email": "a@b.c",
                                "date": "2026-06-01T00:00:00Z",
                            },
                        },
                        "author": {"login": "alice"},
                    }
                ],
            )
        if path.endswith("/issues") or path.endswith("/pulls") or path.endswith("/contents/"):
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={"message": "missing"})

    call = await open_rest_session("gho_oauth", client=_client(handler))
    snap = await collect_github(
        CollectRequest(
            job_id="job-1",
            repos=[RepoRef(owner="acme", name="demo")],
            policy=CollectPolicy(needs=["activity"]),
        ),
        "gho_oauth",
        call_tool=call,
    )
    assert snap.viewer_login == "alice"
    assert snap.repos[0].head_sha == SHA
    assert snap.repos[0].commits[0].message == "feat: init"


def test_http_status_reads_exception_group() -> None:
    inner = GithubRestError(401, "github api 401 /user")
    group = ExceptionGroup("unhandled errors in a TaskGroup", [inner])
    assert http_status(group, str(group)) == 401
