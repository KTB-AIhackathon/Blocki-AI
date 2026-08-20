from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app import pipelines
from app.analyze import analyze
from app.analyze.til import facts_of
from app.contracts import (
    DocumentSpec,
    GitHubSnapshot,
    JobRequest,
    NotionSnapshot,
    ProfileFields,
    TilEntry,
)
from app.llm import guard


def empty_snapshot() -> GitHubSnapshot:
    return GitHubSnapshot(
        collected_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
        complete=True,
        snapshot_digest="d" * 64,
    )


def til_snapshot() -> NotionSnapshot:
    return NotionSnapshot(
        entries=[
            TilEntry(
                date=date(2026, 8, 20),
                title="2026-08-20 · 캐시 개선",
                body_markdown="## 결과\n\n응답 시간이 줄었다.",
                page_id="page-a",
                tags=["backend", "cache"],
            )
        ],
        complete=True,
    )


def test_til_conversion_has_stable_grounding_ids() -> None:
    facts = facts_of(til_snapshot())

    assert [fact.id for fact in facts] == ["til:page-a"]
    assert facts[0].title == "2026-08-20 · 캐시 개선"
    assert facts[0].body_markdown == "## 결과\n\n응답 시간이 줄었다."
    assert facts[0].tags == ["backend", "cache"]


def test_analyze_adds_til_facts_and_digest_input() -> None:
    evidence = analyze(empty_snapshot(), til=til_snapshot())

    assert [fact.id for fact in evidence.til] == ["til:page-a"]
    assert evidence.ids() == {"til:page-a"}
    assert guard._digest(evidence)["til"][0]["id"] == "til:page-a"


def test_analyze_with_til_none_delegates_to_existing_behavior() -> None:
    snapshot = empty_snapshot()
    assert analyze(snapshot, til=None).model_dump() == {
        "viewer": {"login": None, "aliases": []},
        "projects": [],
        "skills": [],
        "period_start": None,
        "period_end": None,
        "my_commits": 0,
        "complete": True,
        "warnings": [],
        "til": [],
    }


@pytest.mark.asyncio
async def test_run_with_til_none_keeps_existing_behavior() -> None:
    job = JobRequest(
        job_id="j1",
        user_id="u1",
        job_type="portfolio",
        document=DocumentSpec(kind="portfolio", profile_fields=ProfileFields(name="홍길동")),
    )
    implicit = await pipelines.run(job, empty_snapshot())
    actual = await pipelines.run(job, empty_snapshot(), til=None)

    assert actual.model_dump(exclude={"proposal_id", "proposal_digest"}) == implicit.model_dump(
        exclude={"proposal_id", "proposal_digest"}
    )
