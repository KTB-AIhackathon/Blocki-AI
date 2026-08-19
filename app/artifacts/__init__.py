from __future__ import annotations

from uuid import uuid4

from app.contracts import ArtifactProposal, GitHubSnapshot, JobError, JobRequest, fill_proposal_digests

from . import profile, progress, readme

__all__ = ["build_artifact"]


def build_artifact(snapshot: GitHubSnapshot, job: JobRequest, llm=None) -> ArtifactProposal:
    if job.job_type == "progress_summary":
        proposal = progress.build(snapshot, job, llm)
    elif job.job_type == "profile_document":
        proposal = profile.build(snapshot, job, llm)
    elif job.job_type == "readme_proposal":
        proposal = readme.build(snapshot, job, llm)
    else:
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
    proposal.proposal_id = str(uuid4())
    return fill_proposal_digests(proposal, snapshot.snapshot_digest)
