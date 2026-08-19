from datetime import datetime, timezone

from app.artifacts import build_artifact
from app.collect.github import collect_github
from app.contracts import (
    CollectRequest,
    DocumentSpec,
    ExecuteRequest,
    JobRequest,
    ProfileFields,
    ReadmeTarget,
    RepoRef,
    action_digest_of,
)
from app.execute.readme_pr import execute_readme_pr

SHA = "abc123def456"
PAT = "gho_pipeline_pat"


class Fake:
    def __init__(self) -> None:
        self.prs: list[dict] = []
        self.branches: dict[str, str] = {}
        self.files = {("README.md", "main"): {"blob_sha": "blob1", "content": "# old\n"}}
        self.refs = {"main": SHA}

    async def __call__(self, name: str, args: dict):
        if name == "get_me":
            return {"login": "alice"}
        if name == "get_repo_meta":
            return {
                "default_branch": "main",
                "head_sha": SHA,
                "description": "demo",
                "topics": ["python"],
                "languages": [{"name": "Python", "bytes": 10}],
                "manifest_files": ["pyproject.toml"],
            }
        if name == "list_commits":
            return [
                {
                    "sha": SHA,
                    "message": "init",
                    "author": "alice",
                    "committed_at": "2026-01-01T00:00:00Z",
                }
            ]
        if name in ("list_issues", "list_pull_requests"):
            return []
        if name == "get_file":
            path = args.get("path") or "README.md"
            ref = args.get("ref") or "main"
            if "path" in args and "ref" not in args:
                return {
                    "path": path,
                    "blob_sha": "blob1",
                    "content": "# old\n",
                }
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
            url = "https://github.com/acme/demo/pull/1"
            self.prs.append({"url": url, "state": "open", "number": 1})
            return {"url": url}
        raise AssertionError(name)


async def test_progress_collect_then_build():
    fake = Fake()
    snap = await collect_github(
        CollectRequest(
            job_id="j1",
            repos=[RepoRef(owner="acme", name="demo")],
            needs=["activity"],
        ),
        PAT,
        call_tool=fake,
    )
    job = JobRequest(job_id="j1", user_id="u1", job_type="progress_summary")
    proposal = build_artifact(snap, job)
    assert proposal.status == "proposed"
    assert "init" in proposal.body_markdown
    assert PAT not in proposal.body_markdown
    assert proposal.proposal_digest


async def test_profile_and_readme_then_execute():
    fake = Fake()
    snap = await collect_github(
        CollectRequest(
            job_id="j2",
            repos=[RepoRef(owner="acme", name="demo")],
            needs=["activity", "profile_evidence", "readme"],
            readme_path="README.md",
        ),
        PAT,
        call_tool=fake,
    )
    portfolio = build_artifact(
        snap,
        JobRequest(
            job_id="j2",
            user_id="u1",
            job_type="profile_document",
            document=DocumentSpec(
                kind="portfolio",
                profile_fields=ProfileFields(name="홍길동", contact_md="me@a.com"),
            ),
        ),
    )
    assert portfolio.status == "proposed"
    assert "홍길동" in portfolio.body_markdown
    assert "Python" in portfolio.body_markdown

    readme = build_artifact(
        snap,
        JobRequest(
            job_id="j3",
            user_id="u1",
            job_type="readme_proposal",
            readme=ReadmeTarget(owner="acme", repo="demo", path="README.md"),
        ),
    )
    assert readme.status == "proposed"
    assert readme.proposed_action is not None
    action = readme.proposed_action.model_copy(update={"expected_base_sha": SHA})
    result = await execute_readme_pr(
        ExecuteRequest(
            execution_id="e1",
            proposal_id=readme.proposal_id,
            action_digest=action_digest_of(action),
            action=action,
            idempotency_key=readme.proposal_id,
        ),
        PAT,
        call_tool=fake,
    )
    assert result.status == "created"
    assert result.pr_url
    again = await execute_readme_pr(
        ExecuteRequest(
            execution_id="e2",
            proposal_id=readme.proposal_id,
            action_digest=action_digest_of(action),
            action=action,
            idempotency_key=readme.proposal_id,
        ),
        PAT,
        call_tool=fake,
    )
    assert again.status == "duplicate"
