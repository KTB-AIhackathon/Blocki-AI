from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, PrivateAttr, computed_field, model_validator

from app.contracts.common import JobError, sha256_hex
from app.contracts.evidence import EvidenceRef
from app.contracts.execute import action_digest_of
from app.contracts.github import RepoCursor, RepoRef, SnapshotSummary
from app.contracts.readme import ReadmePrAction, ReadmeTarget

DocumentKind = Literal["portfolio", "resume"]
ArtifactKind = Literal["progress", "portfolio", "resume", "readme"]
ProposalStatus = Literal["proposed", "no_change", "partial", "blocked", "failed"]

# "profile_document" is the pre-split name Spring may still send. JobRequest
# rewrites it to document.kind so nothing downstream has to know about it.
JobType = Literal[
    "progress_summary",
    "portfolio",
    "resume",
    "readme_proposal",
    "profile_document",
]
LEGACY_DOCUMENT_JOB_TYPE = "profile_document"
DOCUMENT_JOB_TYPES: tuple[str, ...] = ("portfolio", "resume")

ARTIFACT_TITLES: dict[str, str] = {
    "progress": "진행 메모",
    "portfolio": "포트폴리오",
    "resume": "이력서",
    "readme": "README 제안",
}


class ProfileFields(BaseModel):
    """Values the user typed into Spring. Never inferred from GitHub."""

    name: str = ""
    contact_md: str = ""
    experience_md: str = ""
    education_md: str = ""


class DocumentSpec(BaseModel):
    kind: DocumentKind
    template_version: str = "v1"
    profile_fields: ProfileFields = Field(default_factory=ProfileFields)


class NotionTarget(BaseModel):
    #: The Developer TIL Dashboard page id Spring got from `ensure`. Anything
    #: else — an OAuth workspace id, a stale page — is refused at write time.
    parent_id: str | None = None
    log_date: date | None = None
    title: str | None = None


class NotionEnsureRequest(BaseModel):
    """Spring asks for the dashboard once, right after the user connects Notion."""

    user_id: str
    #: What Spring already stored, so a returning user costs one fetch and no
    #: search. Absent on the first connect.
    known_page_id: str | None = None


class NotionEnsureResult(BaseModel):
    ok: bool
    page_id: str | None = None
    page_url: str | None = None
    created: bool = False
    error: JobError | None = None


class JobRequest(BaseModel):
    job_id: str
    user_id: str
    job_type: JobType
    repos: list[RepoRef] = Field(default_factory=list)
    since: datetime | None = None
    cursor: list[RepoCursor] | None = None
    document: DocumentSpec | None = None
    readme: ReadmeTarget | None = None
    notion: NotionTarget | None = None

    @model_validator(mode="after")
    def _normalize_job_type(self) -> JobRequest:
        if self.job_type == LEGACY_DOCUMENT_JOB_TYPE and self.document is not None:
            self.job_type = self.document.kind
        return self

    @property
    def is_document(self) -> bool:
        return self.job_type in DOCUMENT_JOB_TYPES


class TemplateRef(BaseModel):
    kind: DocumentKind
    version: str
    sha256: str


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
    _publish_briefs: list[dict[str, str]] = PrivateAttr(default_factory=list)
    _hub_tail: str = PrivateAttr(default="")


class ArtifactPayload(BaseModel):
    """Spring DB insert payload. FastAPI never stores this."""

    kind: ArtifactKind
    title: str
    body_markdown: str
    content_type: str = "text/markdown"
    proposal_id: str
    template_ref: TemplateRef | None = None


class NotionWriteResult(BaseModel):
    attempted: bool = False
    ok: bool = False
    page_id: str | None = None
    page_url: str | None = None
    skipped_reason: str | None = None
    error: JobError | None = None


class JobResult(BaseModel):
    """What Spring reads back from `POST /internal/jobs`.

    The three computed fields restate `proposal.status`, `error.code` and the
    snapshot at the top level, because that is where
    `DocumentGenerationClient.validate` looks and it rejects the whole response
    if they are missing or unexpected. They are derived rather than stored so
    they cannot drift from the proposal they describe.
    """

    job_id: str
    ok: bool
    proposal: ArtifactProposal | None = None
    artifact: ArtifactPayload | None = None
    notion: NotionWriteResult | None = None
    snapshot_summary: SnapshotSummary
    next_cursor: list[RepoCursor] = Field(default_factory=list)
    error: JobError | None = None

    @computed_field
    @property
    def status(self) -> str:
        """The proposal's status, collapsed to the words Spring accepts.

        Spring knows one failure word, so `blocked` — a proposal we declined to
        make, not a crash — arrives as `failed` with the reason in `error_code`.
        """
        if not self.ok:
            return "failed"
        return self.proposal.status if self.proposal else "partial"

    @computed_field
    @property
    def error_code(self) -> str | None:
        """Never blank while `ok` is false; Spring refuses a failure without one."""
        if self.ok:
            return None
        error = self.error or (self.proposal.error if self.proposal else None)
        return error.code if error else "internal"

    @computed_field
    @property
    def missing_sources(self) -> list[str]:
        """`["GITHUB"]` when no repository came back to ground the document.

        GITHUB is the only name Spring will accept, and a document left partial
        by blank career fields does not belong here: the user fills those in,
        GitHub never could.
        """
        return [] if self.snapshot_summary.repo_count else ["GITHUB"]


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


def artifact_from(proposal: ArtifactProposal) -> ArtifactPayload | None:
    if not (proposal.body_markdown or "").strip():
        return None
    return ArtifactPayload(
        kind=proposal.kind,
        title=ARTIFACT_TITLES.get(proposal.kind, proposal.kind),
        body_markdown=proposal.body_markdown,
        proposal_id=proposal.proposal_id,
        template_ref=proposal.template_ref,
    )
