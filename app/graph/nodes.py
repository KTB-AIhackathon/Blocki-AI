from __future__ import annotations

from typing import TYPE_CHECKING

from app import pipelines
from app.collect import collect_github
from app.collect.notion_til import collect_notion_til
from app.contracts import (
    CollectRequest,
    JobRequest,
    NotionSnapshot,
    NotionWriteResult,
    RepoRef,
    artifact_from,
)
from app.publish import publish_artifact

if TYPE_CHECKING:
    from app.graph.build import _State


BLOCKING_STATUSES = ("failed", "blocked")


def collect_node(
    req: JobRequest,
    pipeline: pipelines.Pipeline,
    pat: str,
    repos: list[RepoRef],
    collect_fn=collect_github,
):
    async def collect(_state: _State) -> _State:
        snapshot = await collect_fn(
            CollectRequest(
                job_id=req.job_id,
                repos=repos,
                since=req.since,
                cursor=req.cursor,
                policy=pipeline.policy,
                readme_path=req.readme.path if req.readme else None,
            ),
            pat,
        )
        return {"snapshot": snapshot}

    return collect


def build_node(req: JobRequest):
    async def build(state: _State) -> _State:
        til = state.get("til")
        if til is None:
            proposal = await pipelines.run(req, state["snapshot"])
        else:
            proposal = await pipelines.run(req, state["snapshot"], til=til)
        return {"proposal": proposal, "artifact": artifact_from(proposal)}

    return build


def publish_node(req: JobRequest, notion_token: str, publish_fn=publish_artifact):
    async def publish(state: _State) -> _State:
        proposal = state["proposal"]
        if proposal.status in BLOCKING_STATUSES:
            return {"notion": None}
        if not notion_token:
            return {"notion": NotionWriteResult(skipped_reason="missing_token")}
        result = await publish_fn(
            state.get("artifact"),
            notion_token=notion_token,
            target=req.notion,
        )
        return {"notion": result}

    return publish


def collect_notion_node(req: JobRequest, notion_token: str, collect_fn=collect_notion_til):
    async def collect(_state: _State) -> _State:
        parent_id = req.notion.parent_id if req.notion else ""
        try:
            snapshot = await collect_fn(parent_id, notion_token)
        except Exception as exc:
            snapshot = NotionSnapshot(
                complete=False,
                warnings=[f"Notion TIL collection failed: {type(exc).__name__}"],
            )
        return {"til": snapshot}

    return collect
