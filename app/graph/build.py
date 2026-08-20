from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app import pipelines
from app.collect import collect_github
from app.contracts import (
    ArtifactPayload,
    ArtifactProposal,
    GitHubSnapshot,
    JobRequest,
    NotionSnapshot,
    NotionWriteResult,
    RepoRef,
)
from app.collect.notion_til import collect_notion_til
from app.graph.nodes import build_node, collect_node, collect_notion_node, publish_node
from app.publish import publish_artifact


class _State(TypedDict, total=False):
    snapshot: GitHubSnapshot
    til: NotionSnapshot | None
    proposal: ArtifactProposal
    artifact: ArtifactPayload | None
    briefs: list[dict[str, str]]
    hub_tail: str
    notion: NotionWriteResult | None


def compile_graph(
    req: JobRequest,
    pipeline: pipelines.Pipeline,
    *,
    pat: str,
    notion_token: str,
    repos: list[RepoRef],
    deadline: float | None = None,
    collect_fn=collect_github,
    notion_collect_fn=collect_notion_til,
    publish_fn=publish_artifact,
):
    graph = StateGraph(_State)
    graph.add_node("collect", collect_node(req, pipeline, pat, repos, collect_fn))
    if notion_token:
        graph.add_node(
            "collect_notion", collect_notion_node(req, notion_token, notion_collect_fn)
        )
    graph.add_node("build", build_node(req, deadline=deadline))
    graph.add_node("publish", publish_node(req, notion_token, publish_fn))
    graph.add_edge(START, "collect")
    graph.add_edge("collect", "build")
    if notion_token:
        graph.add_edge(START, "collect_notion")
        graph.add_edge("collect_notion", "build")
    graph.add_edge("build", "publish")
    graph.add_edge("publish", END)
    return graph.compile()
