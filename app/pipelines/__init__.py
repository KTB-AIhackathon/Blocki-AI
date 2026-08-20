"""Pipeline registry.

One folder per artifact, one row here. A pipeline owns its collect policy
because incremental memos and full-history documents need different data from
the same GitHub account.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from app.analyze import analyze
from app.contracts import (
    ArtifactKind,
    ArtifactProposal,
    CollectPolicy,
    Evidence,
    GitHubSnapshot,
    JobError,
    JobRequest,
    fill_proposal_digests,
)
from app.pipelines import portfolio, progress, readme, resume

Builder = Callable[..., Awaitable[ArtifactProposal]]

# Documents need the full history, so neither a cursor nor a caller's `since`
# may narrow it. They also keep other people's commits: the analyze layer counts
# only the user's own, and dropping the rest here would make every team
# repository look like solo work.
DOCUMENT_POLICY = CollectPolicy(
    needs=["profile_evidence", "activity"],
    use_cursor=False,
    full_history=True,
    author_only=False,
    max_repos=6,
    max_commits=100,
    max_issues=30,
    max_prs=30,
)


@dataclass(frozen=True)
class EvidenceSpec:
    max_projects: int
    max_highlights: int
    require_own_commits: bool = True


@dataclass(frozen=True)
class Pipeline:
    kind: ArtifactKind
    policy: CollectPolicy
    build: Builder
    evidence: EvidenceSpec | None = None
    requires: str | None = None


REGISTRY: dict[str, Pipeline] = {
    "progress_summary": Pipeline(
        kind="progress",
        policy=CollectPolicy(needs=["activity"], use_cursor=True),
        build=progress.build,
    ),
    "portfolio": Pipeline(
        kind="portfolio",
        policy=DOCUMENT_POLICY,
        build=portfolio.build,
        evidence=EvidenceSpec(max_projects=5, max_highlights=5),
        requires="document",
    ),
    "resume": Pipeline(
        kind="resume",
        policy=DOCUMENT_POLICY,
        build=resume.build,
        evidence=EvidenceSpec(max_projects=3, max_highlights=3),
        requires="document",
    ),
    "readme_proposal": Pipeline(
        kind="readme",
        policy=CollectPolicy(needs=["readme", "activity"], use_cursor=True),
        build=readme.build,
        requires="readme",
    ),
}


def resolve(job_type: str) -> Pipeline | None:
    return REGISTRY.get(job_type)


def evidence_for(pipeline: Pipeline, snapshot: GitHubSnapshot) -> Evidence:
    spec = pipeline.evidence
    if spec is None:
        return Evidence(complete=snapshot.complete)
    return analyze(
        snapshot,
        max_projects=spec.max_projects,
        max_highlights=spec.max_highlights,
        require_own_commits=spec.require_own_commits,
    )


async def run(
    job: JobRequest, snapshot: GitHubSnapshot, *, llm: Any | None = None
) -> ArtifactProposal:
    pipeline = resolve(job.job_type)
    if pipeline is None:
        proposal = ArtifactProposal(
            proposal_id="",
            job_id=job.job_id,
            status="failed",
            kind="progress",
            error=JobError(
                code="validation",
                message=f"unsupported job_type: {job.job_type}",
                retryable=False,
            ),
        )
    else:
        evidence = evidence_for(pipeline, snapshot)
        proposal = await pipeline.build(job, snapshot, evidence, llm=llm)
    proposal.proposal_id = str(uuid4())
    return fill_proposal_digests(proposal, snapshot.snapshot_digest)


__all__ = [
    "EvidenceSpec",
    "Pipeline",
    "REGISTRY",
    "evidence_for",
    "resolve",
    "run",
]
