from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from app.collect import parse
from app.collect.github import collect_github
from app.collect.mcp import mcp_url
from app.contracts import (
    CollectPolicy,
    CollectRequest,
    GitHubCollectError,
    RepoCursor,
    RepoRef,
    snapshot_digest_of,
)
from tests.conftest import PAT, SHA, FakeGitHub, commit, repo_meta


def request_for(policy: CollectPolicy, **kwargs) -> CollectRequest:
    data = {
        "job_id": "job-1",
        "repos": [RepoRef(owner="acme", name="demo")],
        "policy": policy,
    }
    data.update(kwargs)
    return CollectRequest(**data)


ACTIVITY = CollectPolicy(needs=["activity"])
DOCUMENT = CollectPolicy(
    needs=["activity", "profile_evidence"],
    use_cursor=False,
    full_history=True,
    author_only=True,
)


async def test_collects_viewer_and_repo_activity(fake_github: FakeGitHub) -> None:
    snap = await collect_github(request_for(ACTIVITY), PAT, call_tool=fake_github)

    assert snap.viewer_login == "alice"
    assert snap.complete is True
    repo = snap.repos[0]
    assert (repo.owner, repo.name, repo.head_sha) == ("acme", "demo", SHA)
    assert repo.commits[0].message == "feat: init"
    assert snap.next_cursor[0].head_sha == SHA
    assert snap.snapshot_digest == snapshot_digest_of(snap.repos, snap.viewer_login)
    assert PAT not in snap.model_dump_json()
    assert not fake_github.called("list_repos")


async def test_cursor_skips_commits_only_when_policy_allows() -> None:
    cursor = [
        RepoCursor(
            owner="acme",
            name="demo",
            head_sha=SHA,
            last_success_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
    ]
    incremental = FakeGitHub(list_issues=[{"number": 1, "title": "x", "state": "open"}])
    snap = await collect_github(
        request_for(ACTIVITY, cursor=cursor), PAT, call_tool=incremental
    )
    assert snap.repos[0].commits == []
    assert len(snap.repos[0].issues) == 1
    assert not incremental.called("list_commits")

    # Same cursor, document policy: history is the whole point, so it is ignored.
    full = FakeGitHub()
    snap = await collect_github(request_for(DOCUMENT, cursor=cursor), PAT, call_tool=full)
    assert snap.repos[0].commits
    assert full.called("list_commits")


async def test_incremental_jobs_pass_the_callers_time_window() -> None:
    fake = FakeGitHub()
    since = datetime(2026, 5, 1, tzinfo=timezone.utc)

    await collect_github(request_for(ACTIVITY, since=since), PAT, call_tool=fake)

    assert fake.args_for("list_commits")[0]["since"] == parse.iso(since)


async def test_documents_ignore_a_time_window_the_caller_sent() -> None:
    """포트폴리오·이력서는 전체 이력이다. 호출자가 창을 보내도 최근 N일만 읽히면 안 된다."""
    fake = FakeGitHub()
    since = datetime(2026, 5, 1, tzinfo=timezone.utc)

    await collect_github(request_for(DOCUMENT, since=since), PAT, call_tool=fake)

    assert "since" not in fake.args_for("list_commits")[0]
    assert "since" not in fake.args_for("list_issues")[0]


async def test_author_only_drops_other_peoples_commits() -> None:
    fake = FakeGitHub(
        list_commits=[
            commit("a" * 12, "feat: mine", author="alice"),
            commit("b" * 12, "feat: theirs", author="bob"),
        ]
    )
    snap = await collect_github(request_for(DOCUMENT, cursor=None), PAT, call_tool=fake)

    assert [c.author for c in snap.repos[0].commits] == ["alice"]
    assert all(c.mine for c in snap.repos[0].commits)
    assert fake.args_for("list_commits")[0]["author"] == "alice"


async def test_author_only_keeps_everything_when_nothing_matches() -> None:
    """An unrecognised author list means attribution failed, not that we idled."""
    fake = FakeGitHub(list_commits=[commit("c" * 12, "feat: ci", author="dependabot[bot]")])
    snap = await collect_github(request_for(DOCUMENT, cursor=None), PAT, call_tool=fake)

    assert len(snap.repos[0].commits) == 1
    assert snap.repos[0].commits[0].mine is False


async def test_partial_snapshot_when_one_repo_fails() -> None:
    def meta(args):
        if args["name"] == "bad":
            raise RuntimeError("repo missing")
        return repo_meta()

    fake = FakeGitHub(get_repo_meta=meta)
    snap = await collect_github(
        request_for(
            ACTIVITY,
            repos=[RepoRef(owner="acme", name="demo"), RepoRef(owner="acme", name="bad")],
        ),
        PAT,
        call_tool=fake,
    )
    assert snap.complete is False
    assert [r.name for r in snap.repos] == ["demo"]
    assert any("bad" in w for w in snap.warnings)
    assert PAT not in "".join(snap.warnings)


@pytest.mark.parametrize(
    "message,code,retryable",
    [
        ("401 Unauthorized", "github_auth", False),
        ("429 rate limit", "github_rate_limit", True),
        ("403 Forbidden", "github_scope", False),
        ("Resource not accessible by integration", "github_scope", False),
        ("your token has insufficient scope", "github_scope", False),
    ],
)
async def test_fatal_github_errors_are_mapped(message: str, code: str, retryable: bool) -> None:
    fake = FakeGitHub(get_me=RuntimeError(message))
    with pytest.raises(GitHubCollectError) as caught:
        await collect_github(request_for(ACTIVITY), PAT, call_tool=fake)

    assert caught.value.error.code == code
    assert caught.value.error.retryable is retryable
    assert PAT not in str(caught.value)


async def test_a_scope_error_names_the_scope_to_add() -> None:
    """빈 문서만 돌려주면 사용자는 무엇을 고쳐야 할지 알 수 없다."""
    fake = FakeGitHub(get_me=RuntimeError("403 Forbidden"))
    with pytest.raises(GitHubCollectError) as caught:
        await collect_github(request_for(ACTIVITY), PAT, call_tool=fake)

    assert "read:user" in caught.value.error.message
    assert "repo" in caught.value.error.message


async def test_a_repo_we_cannot_read_says_why_it_was_skipped() -> None:
    """비공개 저장소가 조용히 빠지면 문서가 왜 얇은지 알 수 없다."""

    def meta(args):
        if args["name"] == "private":
            raise RuntimeError("403 Forbidden")
        return repo_meta()

    fake = FakeGitHub(get_repo_meta=meta)
    snap = await collect_github(
        request_for(
            ACTIVITY,
            repos=[RepoRef(owner="acme", name="demo"), RepoRef(owner="acme", name="private")],
        ),
        PAT,
        call_tool=fake,
    )

    assert snap.complete is False
    assert [r.name for r in snap.repos] == ["demo"]
    assert any("권한" in w and "private" in w for w in snap.warnings)


async def test_rate_limit_retries_before_giving_up() -> None:
    hits = {"n": 0}

    async def get_me(_args):
        hits["n"] += 1
        if hits["n"] < 3:
            raise RuntimeError("429 Too Many Requests")
        return {"login": "alice"}

    fake = FakeGitHub(get_me=get_me)
    snap = await collect_github(request_for(ACTIVITY), PAT, call_tool=fake)
    assert snap.viewer_login == "alice"
    assert hits["n"] == 3


async def test_missing_readme_does_not_drop_the_repo() -> None:
    fake = FakeGitHub(get_file=RuntimeError("404 Not Found"))
    snap = await collect_github(
        request_for(CollectPolicy(needs=["readme"])), PAT, call_tool=fake
    )
    assert snap.complete is True
    assert snap.repos[0].readme is None


async def test_readme_path_is_forwarded() -> None:
    fake = FakeGitHub()
    fake.files[("docs/README.md", "main")] = {
        "path": "docs/README.md",
        "blob_sha": "fff",
        "content": "# hi",
    }
    snap = await collect_github(
        request_for(CollectPolicy(needs=["readme"]), readme_path="docs/README.md"),
        PAT,
        call_tool=fake,
    )
    assert snap.repos[0].readme is not None
    assert snap.repos[0].readme.path == "docs/README.md"
    assert fake.args_for("get_file")[0]["path"] == "docs/README.md"


async def test_two_pats_never_share_identity() -> None:
    alice = FakeGitHub(
        get_me={"login": "alice"}, list_commits=[commit("a" * 12, "alice-commit", "alice")]
    )
    bob = FakeGitHub(
        get_me={"login": "bob"}, list_commits=[commit("b" * 12, "bob-commit", "bob")]
    )
    a = await collect_github(request_for(ACTIVITY), "pat-alice", call_tool=alice)
    b = await collect_github(request_for(ACTIVITY), "pat-bob", call_tool=bob)

    assert (a.viewer_login, b.viewer_login) == ("alice", "bob")
    assert a.snapshot_digest != b.snapshot_digest
    assert "pat-alice" not in a.model_dump_json()
    assert "pat-bob" not in b.model_dump_json()


def _refs(*names: str) -> list[RepoRef]:
    return [RepoRef(owner="acme", name=name) for name in names]


class _Tracker:
    """Counts how many repositories are being read at the same moment."""

    def __init__(self, delay: float = 0.01) -> None:
        self.delay = delay
        self.now = 0
        self.peak = 0

    async def meta(self, _args):
        self.now += 1
        self.peak = max(self.peak, self.now)
        try:
            await asyncio.sleep(self.delay)
            return repo_meta()
        finally:
            self.now -= 1


async def test_repos_keep_request_order_however_fast_they_answer() -> None:
    """The digest and the project list are built from this order.

    Reading concurrently means the fast repository finishes first. If that decided the order,
    the same account would produce a different digest on every run and Spring would store a
    new version each time nothing changed.
    """

    async def meta(args):
        await asyncio.sleep(0.05 if args["name"] == "slow" else 0)
        return repo_meta()

    snap = await collect_github(
        request_for(ACTIVITY, repos=_refs("slow", "fast")),
        PAT,
        call_tool=FakeGitHub(get_repo_meta=meta),
    )

    assert [r.name for r in snap.repos] == ["slow", "fast"]


async def test_repos_are_read_concurrently() -> None:
    """Six round trips per repository, one repository at a time, took ~200s against a 300s
    timeout on a real account."""
    tracker = _Tracker()

    await collect_github(
        request_for(ACTIVITY, repos=_refs("a", "b", "c")),
        PAT,
        call_tool=FakeGitHub(get_repo_meta=tracker.meta),
    )

    assert tracker.peak > 1


async def test_concurrency_stays_under_the_policy_limit() -> None:
    """GitHub answers a burst with a secondary rate limit, which costs more than it saves."""
    tracker = _Tracker()
    policy = CollectPolicy(needs=["activity"], max_concurrency=2)

    await collect_github(
        request_for(policy, repos=_refs("a", "b", "c", "d", "e")),
        PAT,
        call_tool=FakeGitHub(get_repo_meta=tracker.meta),
    )

    assert tracker.peak == 2


async def test_one_slow_repo_does_not_hide_another_ones_failure() -> None:
    """Gathered failures are reported against the repository they belong to."""

    async def meta(args):
        if args["name"] == "broken":
            raise RuntimeError("403 Forbidden")
        await asyncio.sleep(0.02)
        return repo_meta()

    snap = await collect_github(
        request_for(ACTIVITY, repos=_refs("slow", "broken", "other")),
        PAT,
        call_tool=FakeGitHub(get_repo_meta=meta),
    )

    assert [r.name for r in snap.repos] == ["slow", "other"]
    assert snap.complete is False
    assert any("broken" in w and "권한" in w for w in snap.warnings)


def test_default_mcp_url_is_official_github(monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_MCP_URL", raising=False)
    assert mcp_url() == "https://api.githubcopilot.com/mcp/"


def test_parse_handles_mcp_text_blocks() -> None:
    assert parse.jsonish([{"type": "text", "text": '[{"login":"alice"}]'}]) == [
        {"login": "alice"}
    ]
    assert parse.jsonish(
        [
            {"type": "text", "text": '{"sha":"aaa"}'},
            {"type": "text", "text": '{"sha":"bbb"}'},
        ]
    ) == [{"sha": "aaa"}, {"sha": "bbb"}]


def test_parse_survives_malformed_timestamps() -> None:
    rows = parse.commits([{"sha": "aaa", "message": "x", "committed_at": "not-a-date"}])
    assert rows[0].committed_at is None
