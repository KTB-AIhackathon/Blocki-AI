from app.collect.github import collect_github, mcp_url
from app.contracts import CollectRequest, RepoRef
from app.api.jobs import handle_job
from app.contracts import JobRequest


class FakeWorld:
    def __init__(self, login: str, repo: str, message: str) -> None:
        self.login = login
        self.repo = repo
        self.message = message
        self.pats: list[str] = []

    async def __call__(self, name: str, args: dict):
        if name == "get_me":
            return {"login": self.login}
        if name == "get_repo_meta":
            return {
                "default_branch": "main",
                "head_sha": "a" * 40,
                "description": f"{self.login} repo",
                "topics": [self.login],
                "languages": [{"name": "Python", "bytes": 1}],
                "manifest_files": [],
            }
        if name == "list_commits":
            return [{"sha": "a" * 40, "message": self.message, "author": self.login}]
        if name in ("list_issues", "list_pull_requests"):
            return []
        raise AssertionError(name)


async def test_two_pats_do_not_share_github_identity():
    alice = FakeWorld("alice", "one", "alice-commit")
    bob = FakeWorld("bob", "two", "bob-commit")
    a = await collect_github(
        CollectRequest(job_id="ja", repos=[RepoRef(owner="alice", name="one")], needs=["activity"]),
        "pat-alice",
        call_tool=alice,
    )
    b = await collect_github(
        CollectRequest(job_id="jb", repos=[RepoRef(owner="bob", name="two")], needs=["activity"]),
        "pat-bob",
        call_tool=bob,
    )
    assert a.viewer_login == "alice"
    assert b.viewer_login == "bob"
    assert a.repos[0].commits[0].message == "alice-commit"
    assert b.repos[0].commits[0].message == "bob-commit"
    assert "pat-alice" not in a.model_dump_json()
    assert "pat-bob" not in b.model_dump_json()
    assert a.snapshot_digest != b.snapshot_digest


async def test_handle_job_uses_request_pat_not_previous_job(monkeypatch):
    seen: list[str] = []

    async def fake_collect(req, github_pat, **_kwargs):
        seen.append(github_pat)
        from app.contracts import GitHubSnapshot, utcnow

        return GitHubSnapshot(
            collected_at=utcnow(),
            complete=True,
            snapshot_digest="d" * 64,
            viewer_login=github_pat[-5:],
        )

    def fake_build(snapshot, job, llm=None):
        from app.artifacts.progress import build
        from app.contracts import fill_proposal_digests
        from uuid import uuid4

        p = build(snapshot, job)
        p.proposal_id = str(uuid4())
        return fill_proposal_digests(p, snapshot.snapshot_digest)

    monkeypatch.setattr("app.api.jobs.collect_github", fake_collect)
    monkeypatch.setattr("app.api.jobs.build_artifact", fake_build)

    r1 = await handle_job(
        JobRequest(job_id="1", user_id="u1", job_type="progress_summary"),
        "pat-user-one",
    )
    r2 = await handle_job(
        JobRequest(job_id="2", user_id="u2", job_type="progress_summary"),
        "pat-user-two",
    )
    assert seen == ["pat-user-one", "pat-user-two"]
    assert r1.ok and r2.ok


def test_default_mcp_url_is_official_github():
    import os

    os.environ.pop("GITHUB_MCP_URL", None)
    assert mcp_url() == "https://api.githubcopilot.com/mcp/"
