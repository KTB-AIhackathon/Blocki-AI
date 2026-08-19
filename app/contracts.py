from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

JobType = Literal["progress_summary", "profile_document", "readme_proposal"]
ProposalStatus = Literal["proposed", "no_change", "partial", "blocked", "failed"]
ArtifactKind = Literal["progress", "portfolio", "resume", "readme"]
DocumentKind = Literal["portfolio", "resume"]
Need = Literal["activity", "profile_evidence", "readme"]
ErrorCode = Literal[
    "missing_pat",
    "github_auth",
    "github_rate_limit",
    "mcp_unavailable",
    "llm_failed",
    "blocked",
    "stale_sha",
    "duplicate",
    "internal",
    "validation",
]
ExecuteStatus = Literal["created", "duplicate", "rejected"]

README_PATH_RE = re.compile(r"^(docs/)?README(\.(md|markdown|rst|txt))?$", re.IGNORECASE)

INTERNAL_KEY_HEADER = "X-Internal-Key"
GITHUB_PAT_HEADER = "X-GitHub-Pat"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def is_allowed_readme_path(path: str) -> bool:
    if not path or ".." in path or path.startswith("/") or "\\" in path:
        return False
    return README_PATH_RE.fullmatch(path) is not None


def canonical_dumps(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    raise TypeError(f"unserializable: {type(value)!r}")


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(canonical_dumps(value).encode("utf-8")).hexdigest()


class RepoRef(BaseModel):
    owner: str
    name: str


class RepoCursor(BaseModel):
    owner: str
    name: str
    head_sha: str
    last_success_at: datetime


class ProfileFields(BaseModel):
    name: str = ""
    contact_md: str = ""
    experience_md: str = ""
    education_md: str = ""


class DocumentSpec(BaseModel):
    kind: DocumentKind
    template_version: str = "v1"
    profile_fields: ProfileFields


class ReadmeTarget(BaseModel):
    owner: str
    repo: str
    path: str = "README.md"

    @field_validator("path")
    @classmethod
    def _path_ok(cls, value: str) -> str:
        if not is_allowed_readme_path(value):
            raise ValueError("readme path not allowed")
        return value


class JobRequest(BaseModel):
    job_id: str
    user_id: str
    job_type: JobType
    repos: list[RepoRef] = Field(default_factory=list)
    since: datetime | None = None
    cursor: list[RepoCursor] | None = None
    document: DocumentSpec | None = None
    readme: ReadmeTarget | None = None


class CollectRequest(BaseModel):
    job_id: str
    repos: list[RepoRef] = Field(default_factory=list)
    since: datetime | None = None
    cursor: list[RepoCursor] | None = None
    needs: list[Need] = Field(default_factory=list)
    readme_path: str | None = None


class CommitSummary(BaseModel):
    sha: str
    message: str
    author: str | None = None
    committed_at: datetime | None = None


class IssueSummary(BaseModel):
    number: int
    title: str
    state: str
    updated_at: datetime | None = None


class PrSummary(BaseModel):
    number: int
    title: str
    state: str
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
    topics: list[str] = Field(default_factory=list)
    languages: list[LanguageShare] = Field(default_factory=list)
    manifest_files: list[str] = Field(default_factory=list)
    commits: list[CommitSummary] = Field(default_factory=list)
    issues: list[IssueSummary] = Field(default_factory=list)
    pull_requests: list[PrSummary] = Field(default_factory=list)
    readme: ReadmeBlob | None = None


class GitHubSnapshot(BaseModel):
    collected_at: datetime
    complete: bool
    snapshot_digest: str
    viewer_login: str | None = None
    repos: list[RepoActivity] = Field(default_factory=list)
    next_cursor: list[RepoCursor] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class JobError(BaseModel):
    code: ErrorCode
    message: str
    retryable: bool = False


class TemplateRef(BaseModel):
    kind: DocumentKind
    version: str
    sha256: str


class EvidenceRef(BaseModel):
    field: str
    repo: str
    source_type: str
    source_id: str


class ReadmePrAction(BaseModel):
    type: Literal["create_readme_pr"] = "create_readme_pr"
    owner: str
    repo: str
    path: str
    base_branch: str
    expected_base_sha: str
    expected_blob_sha: str
    replacement_markdown: str
    pr_title: str
    pr_body: str

    @field_validator("path")
    @classmethod
    def _path_ok(cls, value: str) -> str:
        if not is_allowed_readme_path(value):
            raise ValueError("readme path not allowed")
        return value


class ArtifactProposal(BaseModel):
    proposal_id: str
    job_id: str
    status: ProposalStatus
    kind: ArtifactKind
    body_markdown: str = ""
    template_ref: TemplateRef | None = None
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    unresolved_fields: list[str] = Field(default_factory=list)
    proposed_action: ReadmePrAction | None = None
    proposal_digest: str = ""
    action_digest: str | None = None
    warnings: list[str] = Field(default_factory=list)
    error: JobError | None = None


class ArtifactPayload(BaseModel):
    """Spring DB insert payload. FastAPI never stores this."""

    kind: ArtifactKind
    title: str
    body_markdown: str
    content_type: str = "text/markdown"
    proposal_id: str
    template_ref: TemplateRef | None = None


class SnapshotSummary(BaseModel):
    complete: bool
    repo_count: int
    commit_count: int
    issue_count: int
    pr_count: int


class JobResult(BaseModel):
    job_id: str
    ok: bool
    proposal: ArtifactProposal | None = None
    artifact: ArtifactPayload | None = None
    snapshot_summary: SnapshotSummary
    next_cursor: list[RepoCursor] = Field(default_factory=list)
    error: JobError | None = None


class ExecuteRequest(BaseModel):
    execution_id: str
    proposal_id: str
    action_digest: str
    action: ReadmePrAction
    idempotency_key: str


class ExecuteResult(BaseModel):
    execution_id: str
    status: ExecuteStatus
    pr_url: str | None = None
    error: JobError | None = None


class GitHubCollectError(Exception):
    def __init__(self, error: JobError) -> None:
        super().__init__(error.message)
        self.error = error


def snapshot_digest_of(repos: list[RepoActivity], viewer_login: str | None) -> str:
    payload = {
        "viewer_login": viewer_login,
        "repos": [r.model_dump(mode="json") for r in repos],
    }
    return sha256_hex(payload)


def action_digest_of(action: ReadmePrAction) -> str:
    return sha256_hex(action.model_dump(mode="json"))


def fill_proposal_digests(proposal: ArtifactProposal, snapshot_digest: str) -> ArtifactProposal:
    payload = {
        "job_id": proposal.job_id,
        "kind": proposal.kind,
        "body_markdown": proposal.body_markdown,
        "template_ref": None
        if proposal.template_ref is None
        else proposal.template_ref.model_dump(mode="json"),
        "evidence_refs": [e.model_dump(mode="json") for e in proposal.evidence_refs],
        "unresolved_fields": proposal.unresolved_fields,
        "proposed_action": None
        if proposal.proposed_action is None
        else proposal.proposed_action.model_dump(mode="json"),
        "snapshot_digest": snapshot_digest,
    }
    proposal.proposal_digest = sha256_hex(payload)
    proposal.action_digest = (
        action_digest_of(proposal.proposed_action) if proposal.proposed_action else None
    )
    return proposal


def needs_for_job(job: JobRequest) -> list[Need]:
    if job.job_type == "progress_summary":
        return ["activity"]
    if job.job_type == "profile_document":
        return ["profile_evidence", "activity"]
    return ["readme", "activity"]


def snapshot_summary_of(snapshot: GitHubSnapshot) -> SnapshotSummary:
    return SnapshotSummary(
        complete=snapshot.complete,
        repo_count=len(snapshot.repos),
        commit_count=sum(len(r.commits) for r in snapshot.repos),
        issue_count=sum(len(r.issues) for r in snapshot.repos),
        pr_count=sum(len(r.pull_requests) for r in snapshot.repos),
    )
