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


def build_node(req: JobRequest, deadline: float | None = None):
    async def build(state: _State) -> _State:
        kwargs: dict = {}
        if "til" in state:
            kwargs["til"] = state["til"]
        if deadline is not None:
            kwargs["deadline"] = deadline
        proposal = await pipelines.run(req, state["snapshot"], **kwargs)
        out: _State = {"proposal": proposal, "artifact": artifact_from(proposal)}
        briefs = list(getattr(proposal, "_publish_briefs", []) or [])
        if briefs:
            out["briefs"] = briefs
        tail = getattr(proposal, "_hub_tail", "") or ""
        if tail:
            out["hub_tail"] = tail
        return out

    return build


def publish_node(req: JobRequest, notion_token: str, publish_fn=publish_artifact):
    async def publish(state: _State) -> _State:
        proposal = state["proposal"]
        if proposal.status in BLOCKING_STATUSES:
            return {"notion": None}
        if not notion_token:
            return {"notion": NotionWriteResult(skipped_reason="missing_token")}
        extra: list[str] = []
        kwargs: dict = {}
        if state.get("briefs"):
            kwargs["briefs"] = state["briefs"]
            kwargs["publish_warnings"] = extra
        if state.get("hub_tail"):
            kwargs["hub_tail"] = state["hub_tail"]
        result = await publish_fn(
            state.get("artifact"),
            notion_token=notion_token,
            target=req.notion,
            **kwargs,
        )
        if extra:
            proposal = proposal.model_copy(
                update={"warnings": [*proposal.warnings, *extra]}
            )
            return {"notion": result, "proposal": proposal}
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
