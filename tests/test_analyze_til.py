from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app import pipelines
from app.analyze import analyze
from app.analyze.til import facts_of
from app.contracts import (
    DocumentSpec,
    Evidence,
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


def test_til_conversion_parses_daily_template_fields_and_field_ids() -> None:
    snapshot = NotionSnapshot(
        entries=[
            TilEntry(
                date=date(2026, 8, 20),
                title="기록 제목은 필드가 아니다",
                body_markdown=(
                    "## 🎯 스크럼\n\n"
                    "- [ ] **오늘의 목표:** 캐시 병목을 줄인다.\n\n"
                    "## 🛠️ 오늘 작업한 내용\n\n"
                    "- **문제 또는 목표:** 응답이 느리다.\n"
                    "- **내가 한 일:** 캐시를 붙였다.\n"
                    "- **선택한 방법과 이유:** 키 기반 캐시가 단순해서 선택했다.\n"
                    "- **결과:** 응답이 빨라졌다.\n\n"
                    "## 🧩 오늘의 도전 과제와 해결 방법\n\n"
                    "- **문제:** 반복 조회가 병목이었다.\n"
                    "- **원인:** 매번 원본 API를 호출했다.\n"
                    "- **시도:** TTL을 적용했다.\n"
                    "- **최종 해결:** 캐시 만료 정책을 정했다.\n\n"
                    "<callout>\n"
                    "- **Before:** 900\n"
                    "- **After:** 300\n"
                    "- **단위:** ms\n"
                    "- **측정 기준:** 동일 요청 10회 평균\n"
                    "</callout>\n\n"
                    "## 📚 새로 배운 내용\n\n"
                    "- **배운 내용:** TTL 캐시의 만료를 이해했다.\n"
                    "- **기존 이해와 달라진 점:** 저장만 하면 끝이라고 생각했지만 만료가 필요했다.\n\n"
                    "## 🪞 오늘의 회고\n\n"
                    "- **자유롭게 작성:** 작은 측정이 방향을 정했다.\n\n"
                    "## 💻 GitHub 작업 근거\n\n"
                    "- **Repository:** https://github.com/acme/cache\n"
                ),
                page_id="page-fields",
            )
        ],
        complete=True,
    )

    fact = facts_of(snapshot)[0]

    assert fact.goal == "캐시 병목을 줄인다."
    assert fact.problem == "응답이 느리다.\n반복 조회가 병목이었다.\n매번 원본 API를 호출했다."
    assert fact.attempt == "캐시를 붙였다.\n키 기반 캐시가 단순해서 선택했다.\nTTL을 적용했다."
    assert fact.result == "응답이 빨라졌다.\n캐시 만료 정책을 정했다."
    assert fact.metric is not None
    assert fact.metric.before == "900"
    assert fact.metric.after == "300"
    assert fact.metric.unit == "ms"
    assert fact.metric.criterion == "동일 요청 10회 평균"
    assert fact.learned == "TTL 캐시의 만료를 이해했다.\n저장만 하면 끝이라고 생각했지만 만료가 필요했다."
    assert fact.retro == "작은 측정이 방향을 정했다."
    assert fact.work_repo == "https://github.com/acme/cache"
    assert {
        "til:page-fields",
        "til:page-fields:goal",
        "til:page-fields:problem",
        "til:page-fields:attempt",
        "til:page-fields:result",
        "til:page-fields:metric",
        "til:page-fields:learned",
        "til:page-fields:retro",
        "til:page-fields:work_repo",
    } <= Evidence(til=[fact]).ids()


def test_til_conversion_skips_unmeasured_metric() -> None:
    snapshot = NotionSnapshot(
        entries=[
            TilEntry(
                date=date(2026, 8, 20),
                title="metric",
                body_markdown="- **Before:** 측정하지 않음\n- **After:** 1\n- **단위:** 회",
                page_id="page-unmeasured",
            )
        ],
        complete=True,
    )

    assert facts_of(snapshot)[0].metric is None


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
