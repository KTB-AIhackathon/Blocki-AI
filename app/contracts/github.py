from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.contracts.common import sha256_hex

Need = Literal["activity", "profile_evidence", "readme"]


class RepoRef(BaseModel):
    owner: str
    name: str


class RepoCursor(BaseModel):
    owner: str
    name: str
    head_sha: str
    last_success_at: datetime


class CollectPolicy(BaseModel):
    """What a pipeline needs from GitHub. Set by the pipeline, not by Spring.

    Incremental jobs follow the cursor; document jobs need the full history, so
    honouring a cursor there would empty the project section.
    """

    needs: list[Need] = Field(default_factory=list)
    use_cursor: bool = True
    # A résumé that only covers the last two weeks is worse than no résumé. The
    # pipeline decides its own window so a caller cannot narrow it by accident.
    full_history: bool = False
    author_only: bool = False
    max_repos: int = 5
    max_commits: int = 30
    max_issues: int = 20
    max_prs: int = 20
    # Six round trips per repository against a remote MCP, so walking repositories one at a
    # time put a six-repo document at ~200s against a 300s job timeout. Bounded rather than
    # unlimited because GitHub answers a burst with a secondary rate limit.
    max_concurrency: int = 4


class CollectRequest(BaseModel):
    job_id: str
    repos: list[RepoRef] = Field(default_factory=list)
    since: datetime | None = None
    cursor: list[RepoCursor] | None = None
    policy: CollectPolicy = Field(default_factory=CollectPolicy)
    readme_path: str | None = None


class CommitSummary(BaseModel):
    sha: str
    message: str
    author: str | None = None
    author_email: str | None = None
    committed_at: datetime | None = None
    mine: bool = False


class IssueSummary(BaseModel):
    number: int
    title: str
    state: str
    # Who opened it and who was put on it. A closed issue only counts as the
    # user's own work if one of these is them.
    author: str | None = None
    assignees: list[str] = Field(default_factory=list)
    updated_at: datetime | None = None


class PrSummary(BaseModel):
    number: int
    title: str
    state: str
    merged: bool = False
    author: str | None = None
    updated_at: datetime | None = None


class LanguageShare(BaseModel):
    name: str
    bytes: int


class ReadmeBlob(BaseModel):
    path: str
    blob_sha: str
    content: str


class RepoActivity(BaseModel):
    owner: str
    name: str
    default_branch: str | None = None
    head_sha: str | None = None
    description: str | None = None
    html_url: str | None = None
    topics: list[str] = Field(default_factory=list)
    languages: list[LanguageShare] = Field(default_factory=list)
    manifest_files: list[str] = Field(default_factory=list)
    fork: bool = False
    archived: bool = False
    stars: int = 0
    pushed_at: datetime | None = None
    commits: list[CommitSummary] = Field(default_factory=list)
    issues: list[IssueSummary] = Field(default_factory=list)
    pull_requests: list[PrSummary] = Field(default_factory=list)
    readme: ReadmeBlob | None = None

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


class GitHubSnapshot(BaseModel):
    collected_at: datetime
    complete: bool
    snapshot_digest: str
    viewer_login: str | None = None
    repos: list[RepoActivity] = Field(default_factory=list)
    next_cursor: list[RepoCursor] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class SnapshotSummary(BaseModel):
    complete: bool
    repo_count: int
    commit_count: int
    issue_count: int
    pr_count: int


def snapshot_digest_of(repos: list[RepoActivity], viewer_login: str | None) -> str:
    payload = {
        "viewer_login": viewer_login,
        "repos": [r.model_dump(mode="json") for r in repos],
    }
    return sha256_hex(payload)


def snapshot_summary_of(snapshot: GitHubSnapshot) -> SnapshotSummary:
    return SnapshotSummary(
        complete=snapshot.complete,
        repo_count=len(snapshot.repos),
        commit_count=sum(len(r.commits) for r in snapshot.repos),
        issue_count=sum(len(r.issues) for r in snapshot.repos),
        pr_count=sum(len(r.pull_requests) for r in snapshot.repos),
    )
