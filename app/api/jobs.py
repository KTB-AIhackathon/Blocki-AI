from __future__ import annotations

import asyncio
import inspect
import os
import secrets
from collections.abc import Awaitable
from typing import Annotated, TypedDict, TypeVar

from fastapi import APIRouter, Depends, Header, HTTPException
from langgraph.graph import END, START, StateGraph
from pydantic import model_validator

from app.artifacts import build_artifact
from app.collect.github import collect_github
from app.contracts import (
    ArtifactPayload,
    ArtifactProposal,
    CollectRequest,
    GITHUB_PAT_HEADER,
    GitHubCollectError,
    GitHubSnapshot,
    INTERNAL_KEY_HEADER,
    JobError,
    JobRequest,
    JobResult,
    RepoRef,
    SnapshotSummary,
    fill_proposal_digests,
    needs_for_job,
    snapshot_summary_of,
)

router = APIRouter()

T = TypeVar("T")


class JobIngressRequest(JobRequest):
    @model_validator(mode="after")
    def require_type_payload(self) -> JobIngressRequest:
        if self.job_type == "profile_document" and self.document is None:
            raise ValueError("document is required for profile_document")
        if self.job_type == "readme_proposal" and self.readme is None:
            raise ValueError("readme is required for readme_proposal")
        return self


class _JobState(TypedDict, total=False):
    snapshot: GitHubSnapshot
    proposal: ArtifactProposal


async def _maybe_await(value: T | Awaitable[T]) -> T:
    if inspect.isawaitable(value):
        return await value
    return value


def _require_internal_key(
    x_internal_key: Annotated[str | None, Header(alias=INTERNAL_KEY_HEADER)] = None,
) -> None:
    expected = os.environ.get("INTERNAL_API_KEY")
    if not expected and os.environ.get("PYTEST_CURRENT_TEST"):
        expected = "dev-internal-key"
    if not expected:
        raise HTTPException(status_code=503, detail="INTERNAL_API_KEY not set")
    if x_internal_key is None or not secrets.compare_digest(x_internal_key, expected):
        raise HTTPException(status_code=401, detail="unauthorized")


def _empty_summary() -> SnapshotSummary:
    return SnapshotSummary(
        complete=False,
        repo_count=0,
        commit_count=0,
        issue_count=0,
        pr_count=0,
    )


def _missing_pat_error() -> JobError:
    return JobError(code="missing_pat", message="GitHub PAT header is required", retryable=False)


def _artifact_from(proposal: ArtifactProposal) -> ArtifactPayload | None:
    if not (proposal.body_markdown or "").strip():
        return None
    titles = {
        "progress": "진행 메모",
        "portfolio": "포트폴리오",
        "resume": "이력서",
        "readme": "README 제안",
    }
    return ArtifactPayload(
        kind=proposal.kind,
        title=titles.get(proposal.kind, proposal.kind),
        body_markdown=proposal.body_markdown,
        proposal_id=proposal.proposal_id,
        template_ref=proposal.template_ref,
    )


async def handle_job(req: JobRequest, github_pat: str) -> JobResult:
    pat = (github_pat or "").strip()
    if not pat:
        return JobResult(
            job_id=req.job_id,
            ok=False,
            snapshot_summary=_empty_summary(),
            next_cursor=[],
            error=_missing_pat_error(),
        )

    # PAT is closed over by the nodes; it must not enter graph state.
    async def collect_node(_state: _JobState) -> dict[str, GitHubSnapshot]:
        repos = list(req.repos)
        if req.readme is not None:
            target = RepoRef(owner=req.readme.owner, name=req.readme.repo)
            if not any(r.owner == target.owner and r.name == target.name for r in repos):
                repos = [target, *repos]
        snapshot = await _maybe_await(
            collect_github(
                CollectRequest(
                    job_id=req.job_id,
                    repos=repos,
                    since=req.since,
                    cursor=req.cursor,
                    needs=needs_for_job(req),
                    readme_path=req.readme.path if req.readme else None,
                ),
                pat,
            )
        )
        return {"snapshot": snapshot}

    async def build_node(state: _JobState) -> dict[str, ArtifactProposal]:
        snapshot = state["snapshot"]
        proposal = await _maybe_await(build_artifact(snapshot, req, llm=None))
        fill_proposal_digests(proposal, snapshot.snapshot_digest)
        return {"proposal": proposal}

    graph = StateGraph(_JobState)
    graph.add_node("collect", collect_node)
    graph.add_node("build", build_node)
    graph.add_edge(START, "collect")
    graph.add_edge("collect", "build")
    graph.add_edge("build", END)

    try:
        out = await asyncio.wait_for(
            graph.compile().ainvoke({}),
            timeout=float(os.environ.get("JOB_TIMEOUT", "60")),
        )
    except GitHubCollectError as exc:
        return JobResult(
            job_id=req.job_id,
            ok=False,
            snapshot_summary=_empty_summary(),
            error=exc.error,
        )
    except asyncio.TimeoutError:
        return JobResult(
            job_id=req.job_id,
            ok=False,
            snapshot_summary=_empty_summary(),
            error=JobError(code="internal", message="job timed out", retryable=True),
        )
    except Exception:
        return JobResult(
            job_id=req.job_id,
            ok=False,
            snapshot_summary=_empty_summary(),
            error=JobError(code="internal", message="job failed", retryable=False),
        )

    snapshot = out["snapshot"]
    proposal = out["proposal"]
    ok = proposal.status not in ("failed", "blocked")
    artifact = _artifact_from(proposal) if ok else None
    return JobResult(
        job_id=req.job_id,
        ok=ok,
        proposal=proposal,
        artifact=artifact,
        snapshot_summary=snapshot_summary_of(snapshot),
        next_cursor=list(snapshot.next_cursor) if snapshot.complete else [],
        error=None if ok else proposal.error,
    )


@router.post("/internal/jobs", response_model=JobResult)
async def post_job(
    req: JobIngressRequest,
    x_github_pat: Annotated[str | None, Header(alias=GITHUB_PAT_HEADER)] = None,
    _: None = Depends(_require_internal_key),
) -> JobResult:
    return await handle_job(req, x_github_pat or "")
