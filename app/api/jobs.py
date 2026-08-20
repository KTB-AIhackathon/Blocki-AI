"""POST /internal/jobs — the only entry point Spring uses for generation.

Secrets arrive as headers, stay in closures, and never enter the graph state,
the request body, the response, or a log line.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import TypedDict

from fastapi import APIRouter
from langgraph.graph import END, START, StateGraph
from pydantic import model_validator

from app import pipelines
from app.api.deps import GitHubPat, InternalKey, NotionToken
from app.collect import collect_github
from app.contracts import (
    ArtifactPayload,
    ArtifactProposal,
    CollectRequest,
    GitHubCollectError,
    GitHubSnapshot,
    JobError,
    JobRequest,
    JobResult,
    NotionWriteResult,
    RepoRef,
    SnapshotSummary,
    artifact_from,
    snapshot_summary_of,
)
from app.publish import publish_artifact

router = APIRouter()

BLOCKING_STATUSES = ("failed", "blocked")


class JobIngressRequest(JobRequest):
    @model_validator(mode="after")
    def _require_type_payload(self) -> JobIngressRequest:
        pipeline = pipelines.resolve(self.job_type)
        if pipeline is None:
            raise ValueError(f"unsupported job_type: {self.job_type}")
        if pipeline.requires == "document" and self.document is None:
            raise ValueError(f"document is required for {self.job_type}")
        if pipeline.requires == "readme" and self.readme is None:
            raise ValueError("readme is required for readme_proposal")
        return self


class _State(TypedDict, total=False):
    snapshot: GitHubSnapshot
    proposal: ArtifactProposal
    artifact: ArtifactPayload | None
    notion: NotionWriteResult | None


@router.post("/internal/jobs", response_model=JobResult)
async def post_job(
    req: JobIngressRequest,
    x_github_pat: GitHubPat = None,
    x_notion_token: NotionToken = None,
    _: None = InternalKey,
) -> JobResult:
    return await handle_job(req, x_github_pat or "", x_notion_token or "")


async def handle_job(req: JobRequest, github_pat: str, notion_token: str = "") -> JobResult:
    pat = (github_pat or "").strip()
    if not pat:
        return _failed(
            req.job_id,
            JobError(code="missing_pat", message="GitHub PAT header is required"),
        )

    pipeline = pipelines.resolve(req.job_type)
    if pipeline is None:
        return _failed(
            req.job_id,
            JobError(code="validation", message=f"unsupported job_type: {req.job_type}"),
        )

    timeout = float(os.environ.get("JOB_TIMEOUT", pipeline.timeout_seconds))
    deadline = time.monotonic() + timeout
    graph = _compile(
        req, pipeline, pat=pat, notion_token=(notion_token or "").strip(), deadline=deadline
    )
    try:
        out = await asyncio.wait_for(graph.ainvoke({}), timeout=timeout)
    except GitHubCollectError as exc:
        return _failed(req.job_id, exc.error)
    except asyncio.TimeoutError:
        return _failed(
            req.job_id, JobError(code="internal", message="job timed out", retryable=True)
        )
    except Exception:
        return _failed(req.job_id, JobError(code="internal", message="job failed"))

    snapshot: GitHubSnapshot = out["snapshot"]
    proposal: ArtifactProposal = out["proposal"]
    ok = proposal.status not in BLOCKING_STATUSES
    return JobResult(
        job_id=req.job_id,
        ok=ok,
        proposal=proposal,
        artifact=out.get("artifact") if ok else None,
        notion=out.get("notion"),
        snapshot_summary=snapshot_summary_of(snapshot),
        next_cursor=list(snapshot.next_cursor) if snapshot.complete else [],
        error=None if ok else proposal.error,
    )


def _compile(
    req: JobRequest,
    pipeline: pipelines.Pipeline,
    *,
    pat: str,
    notion_token: str,
    deadline: float | None = None,
):
    async def collect(_state: _State) -> _State:
        snapshot = await collect_github(
            CollectRequest(
                job_id=req.job_id,
                repos=_repos(req),
                since=req.since,
                cursor=req.cursor,
                policy=pipeline.policy,
                readme_path=req.readme.path if req.readme else None,
            ),
            pat,
        )
        return {"snapshot": snapshot}

    async def build(state: _State) -> _State:
        proposal = await pipelines.run(req, state["snapshot"], deadline=deadline)
        return {"proposal": proposal, "artifact": artifact_from(proposal)}

    async def publish(state: _State) -> _State:
        proposal = state["proposal"]
        if proposal.status in BLOCKING_STATUSES:
            return {"notion": None}
        if not notion_token:
            return {"notion": NotionWriteResult(skipped_reason="missing_token")}
        result = await publish_artifact(
            state.get("artifact"),
            notion_token=notion_token,
            target=req.notion,
        )
        return {"notion": result}

    graph = StateGraph(_State)
    graph.add_node("collect", collect)
    graph.add_node("build", build)
    graph.add_node("publish", publish)
    graph.add_edge(START, "collect")
    graph.add_edge("collect", "build")
    graph.add_edge("build", "publish")
    graph.add_edge("publish", END)
    return graph.compile()


def _repos(req: JobRequest) -> list[RepoRef]:
    repos = list(req.repos)
    if req.readme is None:
        return repos
    target = RepoRef(owner=req.readme.owner, name=req.readme.repo)
    if any(r.owner == target.owner and r.name == target.name for r in repos):
        return repos
    return [target, *repos]


def _failed(job_id: str, error: JobError) -> JobResult:
    return JobResult(
        job_id=job_id,
        ok=False,
        snapshot_summary=SnapshotSummary(
            complete=False, repo_count=0, commit_count=0, issue_count=0, pr_count=0
        ),
        next_cursor=[],
        error=error,
    )
