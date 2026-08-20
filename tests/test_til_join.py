from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.analyze import join, repos
from app.contracts import (
    CommitSummary,
    DocumentSpec,
    Evidence,
    GitHubSnapshot,
    JobRequest,
    ProfileFields,
    ProjectFacts,
    ReadmeBlob,
    RepoActivity,
    TilFact,
)
from app.pipelines.portfolio.build import build


NOW = datetime(2026, 8, 20, tzinfo=timezone.utc)


def til(title: str, *, body: str = "", day: date = date(2026, 8, 1)) -> TilFact:
    return TilFact(
        id=f"til:{title}",
        date=day,
        title=title,
        body_markdown=body,
        page_id=title,
    )


def project(repo: str, *, description: str | None = None, **values) -> ProjectFacts:
    return ProjectFacts(id=f"repo:{repo}", repo=repo, description=description, **values)


def test_confirmed_link_wins_over_dates() -> None:
    facts = [
        project(
            "acme/demo",
            started_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
            ended_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
        )
    ]
    entry = til(
        "오래된 기록",
        body="작업 저장소: https://github.com/acme/demo",
        day=date(2025, 1, 1),
    )

    assert join.attach(facts, [entry]) == []
    assert facts[0].til == [entry]


def test_strong_match_uses_korean_description() -> None:
    facts = [
        project(
            "Subin9227/princess-secretary",
            description="LLM 기반 디스코드 집사 봇 '공주비서' 🎀",
        )
    ]
    entry = til("공주비서 운영 기록")

    assert join.attach(facts, [entry]) == []
    assert facts[0].til == [entry]


def test_single_metadata_token_does_not_join() -> None:
    facts = [project("acme/demo-api", description="다른 설명")]
    entry = til("api 기록")

    assert join.attach(facts, [entry]) == [entry]
    assert facts[0].til == []


def test_period_only_does_not_join_even_when_unique() -> None:
    facts = [
        project(
            "acme/one",
            started_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
            ended_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
        )
    ]
    entry = til("날짜만 맞는 기록", day=date(2026, 8, 7))

    assert join.attach(facts, [entry]) == [entry]
    assert facts[0].til == []


def test_short_repo_name_in_title_joins() -> None:
    facts = [project("acme/princess-secretary")]
    entry = til("princess-secretary 운영 기록")

    assert join.attach(facts, [entry]) == []
    assert facts[0].til == [entry]


def test_weak_match_is_discarded_when_dates_are_ambiguous() -> None:
    facts = [
        project(
            "acme/one",
            started_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
            ended_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
        ),
        project(
            "acme/two",
            started_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
            ended_at=datetime(2026, 8, 15, tzinfo=timezone.utc),
        ),
    ]
    entry = til("날짜만 맞는 기록", day=date(2026, 8, 7))

    assert join.attach(facts, [entry]) == [entry]
    assert all(not fact.til for fact in facts)


def test_team_award_repo_outranks_100_commit_study_log() -> None:
    study = ProjectFacts(
        id="repo:acme/study-log",
        repo="acme/study-log",
        my_commits=100,
        total_commits=100,
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ended_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    team = ProjectFacts(
        id="repo:acme/winner",
        repo="acme/winner",
        my_commits=5,
        total_commits=8,
        contributors=3,
        started_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        ended_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        til=[til("회고 1"), til("회고 2"), til("회고 3")],
    )
    study_repo = RepoActivity(owner="acme", name="study-log")
    team_repo = RepoActivity(
        owner="acme",
        name="winner",
        description="우수상 수상 프로젝트",
        readme=ReadmeBlob(path="README.md", blob_sha="r", content="우수상 수상"),
    )

    study.score = repos._score(study, study_repo, now=NOW)
    team.score = repos._score(team, team_repo, now=NOW)

    assert team.score > study.score


@pytest.mark.asyncio
async def test_portfolio_renders_til_contribution_and_three_projects() -> None:
    learned = til("노션 MCP 연결", day=date(2026, 7, 15))
    projects = [
        ProjectFacts(
            id=f"repo:acme/p{i}",
            repo=f"acme/p{i}",
            url=f"https://github.com/acme/p{i}",
            my_commits=5,
            total_commits=5,
            til=[learned] if i == 0 else [],
        )
        for i in range(3)
    ]
    evidence = Evidence(
        projects=projects,
        til=[learned],
        unmatched_til=[],
        my_commits=15,
    )
    job = JobRequest(
        job_id="j1",
        user_id="u1",
        job_type="portfolio",
        document=DocumentSpec(
            kind="portfolio",
            profile_fields=ProfileFields(name="홍길동", contact_md="- me@example.com"),
        ),
    )
    snapshot = GitHubSnapshot(
        collected_at=NOW,
        complete=True,
        snapshot_digest="d" * 64,
    )

    proposal = await build(job, snapshot, evidence)

    assert [line for line in proposal.body_markdown.splitlines() if line.startswith("### ")] == [
        "### p0",
        "### p1",
        "### p2",
    ]
    assert "**배운 것**" in proposal.body_markdown
    assert learned.title in proposal.body_markdown
    assert "- 기여: 커밋 5개 (전체 5개 중 100%)" in proposal.body_markdown


@pytest.mark.asyncio
async def test_empty_til_has_no_learning_headings() -> None:
    evidence = Evidence(
        projects=[ProjectFacts(id="repo:acme/demo", repo="acme/demo", my_commits=1)]
    )
    job = JobRequest(
        job_id="j1",
        user_id="u1",
        job_type="portfolio",
        document=DocumentSpec(
            kind="portfolio",
            profile_fields=ProfileFields(name="홍길동", contact_md="- me@example.com"),
        ),
    )
    snapshot = GitHubSnapshot(
        collected_at=NOW,
        complete=True,
        snapshot_digest="d" * 64,
    )

    proposal = await build(job, snapshot, evidence)

    assert "**배운 것**" not in proposal.body_markdown
    assert "그 외 학습 기록" not in proposal.body_markdown


@pytest.mark.asyncio
async def test_rendered_project_block_contains_meta_and_learning() -> None:
    learned = til("노션 MCP 연결", day=date(2026, 7, 15))
    evidence = Evidence(
        projects=[
            ProjectFacts(
                id="repo:acme/til-project",
                repo="acme/til-project",
                url="https://github.com/acme/til-project",
                started_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
                ended_at=datetime(2026, 8, 31, tzinfo=timezone.utc),
                my_commits=34,
                total_commits=112,
                contributors=4,
                til=[learned],
            )
        ],
        til=[learned],
        my_commits=34,
    )
    job = JobRequest(
        job_id="j1",
        user_id="u1",
        job_type="portfolio",
        document=DocumentSpec(
            kind="portfolio",
            profile_fields=ProfileFields(name="홍길동", contact_md="- me@example.com"),
        ),
    )
    snapshot = GitHubSnapshot(
        collected_at=NOW,
        complete=True,
        snapshot_digest="d" * 64,
    )

    proposal = await build(job, snapshot, evidence)

    assert "- 기간: 2026.07 ~ 2026.08 (2개월)" in proposal.body_markdown
    assert "- 구성: 팀 프로젝트 (4명)" in proposal.body_markdown
    assert "- 기여: 커밋 34개 (전체 112개 중 30%)" in proposal.body_markdown
    assert "**배운 것**" in proposal.body_markdown
