from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app import pipelines
from app.contracts import (
    ArtifactProposal,
    DocumentSpec,
    GitHubSnapshot,
    JobRequest,
    NotionSnapshot,
    NotionWriteResult,
    ProfileFields,
    TilEntry,
)
from app.graph import compile_graph


def request() -> JobRequest:
    return JobRequest(
        job_id="j1",
        user_id="u1",
        job_type="portfolio",
        notion={"parent_id": "dashboard"},
        document=DocumentSpec(kind="portfolio", profile_fields=ProfileFields(name="홍길동")),
    )


def github_snapshot() -> GitHubSnapshot:
    return GitHubSnapshot(
        collected_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
        complete=True,
        snapshot_digest="g" * 64,
    )


def proposal(body: str, warnings: list[str] | None = None) -> ArtifactProposal:
    return ArtifactProposal(
        proposal_id="",
        job_id="j1",
        status="proposed",
        kind="portfolio",
        body_markdown=body,
        warnings=warnings or [],
    )


async def collect_github(_request, _pat) -> GitHubSnapshot:
    return github_snapshot()


async def publish(_artifact, *, notion_token, target) -> NotionWriteResult:
    return NotionWriteResult(attempted=True, ok=True, page_id="page-1")


@pytest.mark.asyncio
async def test_graph_without_notion_token_omits_node_and_til_argument(monkeypatch) -> None:
    calls: list[dict] = []

    async def run(_request, _snapshot, **kwargs):
        calls.append(kwargs)
        return proposal("GitHub fact")

    monkeypatch.setattr(pipelines, "run", run)
    graph = compile_graph(
        request(),
        pipelines.resolve("portfolio"),
        pat="pat",
        notion_token="",
        repos=[],
        collect_fn=collect_github,
        publish_fn=publish,
    )

    compiled = graph.get_graph()
    assert "collect_notion" not in compiled.nodes
    assert {(edge.source, edge.target) for edge in compiled.edges} == {
        ("__start__", "collect"),
        ("collect", "build"),
        ("build", "publish"),
        ("publish", "__end__"),
    }
    out = await graph.ainvoke({})

    assert calls == [{}]
    assert "til" not in out


@pytest.mark.asyncio
async def test_graph_passes_notion_facts_into_built_document(monkeypatch) -> None:
    til = NotionSnapshot(
        entries=[
            TilEntry(
                date=date(2026, 8, 20),
                title="2026-08-20 · 캐시 개선",
                body_markdown="응답 시간이 줄었다.",
                page_id="page-til",
            )
        ],
        complete=True,
    )
    calls: list[dict] = []

    async def collect_notion(_parent_id, _token) -> NotionSnapshot:
        return til

    async def run(_request, _snapshot, **kwargs):
        calls.append(kwargs)
        return proposal(kwargs["til"].entries[0].title)

    monkeypatch.setattr(pipelines, "run", run)
    graph = compile_graph(
        request(),
        pipelines.resolve("portfolio"),
        pat="pat",
        notion_token="notion-token",
        repos=[],
        collect_fn=collect_github,
        notion_collect_fn=collect_notion,
        publish_fn=publish,
    )

    out = await graph.ainvoke({})

    compiled = graph.get_graph()
    assert "collect_notion" in compiled.nodes
    assert {
        ("__start__", "collect_notion"),
        ("collect_notion", "build"),
    } <= {(edge.source, edge.target) for edge in compiled.edges}
    assert calls == [{"til": til}]
    assert out["artifact"].body_markdown == "2026-08-20 · 캐시 개선"
    assert "notion-token" not in repr(out)


@pytest.mark.asyncio
async def test_graph_isolates_notion_collection_failure(monkeypatch) -> None:
    async def collect_notion(_parent_id, _token) -> NotionSnapshot:
        raise RuntimeError("Notion is unavailable")

    async def run(_request, _snapshot, **kwargs):
        til = kwargs["til"]
        return proposal("GitHub fact", warnings=til.warnings)

    monkeypatch.setattr(pipelines, "run", run)
    graph = compile_graph(
        request(),
        pipelines.resolve("portfolio"),
        pat="pat",
        notion_token="notion-token",
        repos=[],
        collect_fn=collect_github,
        notion_collect_fn=collect_notion,
        publish_fn=publish,
    )

    out = await graph.ainvoke({})

    assert out["proposal"].status == "proposed"
    assert out["artifact"].body_markdown == "GitHub fact"
    assert out["proposal"].warnings == [
        "Notion TIL collection failed: RuntimeError"
    ]
