from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.contracts import (
    CommitFact,
    DocumentSpec,
    Evidence,
    GitHubSnapshot,
    JobRequest,
    ProfileFields,
    ProjectFacts,
    ReadmeBlob,
    RepoActivity,
    SkillFact,
    TilFact,
    ViewerIdentity,
    WorkItem,
)
from app.pipelines.portfolio.build import build as build_portfolio
from app.pipelines.resume.build import build as build_resume


NOW = datetime(2026, 8, 20, tzinfo=timezone.utc)
FILL_IN = "아직 비어 있습니다. 이 Notion 페이지에서 직접 채워주세요."


def _til(page: str, repo: str, *, worked: bool = True) -> TilFact:
    return TilFact(
        id=f"til:{page}",
        date=date(2026, 8, 1),
        title=f"{page} 기록",
        body_markdown=f"Repository: {repo}",
        page_id=page,
        goal="반복 작업을 줄인다." if worked else "공부한다.",
        problem="반복 작업이 많다." if worked else "",
        attempt="도구를 만들었다." if worked else "",
        result="반복 작업이 줄었다." if worked else "",
        learned="작은 자동화의 경계를 배웠다." if worked else "",
        retro="다음에는 측정을 먼저 한다." if worked else "",
        work_repo=f"https://github.com/{repo}",
    )


def _project(repo: str, til: list[TilFact] | None = None) -> ProjectFacts:
    return ProjectFacts(
        id=f"repo:{repo}",
        repo=repo,
        url=f"https://github.com/{repo}",
        description="노션 루틴을 기록하는 디스코드 봇",
        started_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        ended_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
        my_commits=61,
        total_commits=61,
        til=til or [],
        languages=[
            SkillFact(
                id=f"skill:python:{repo}",
                name="Python",
                category="language",
                repos=[repo],
            )
        ],
        score=36.9,
    )


def _evidence(projects: list[ProjectFacts]) -> Evidence:
    return Evidence(
        viewer=ViewerIdentity(login="alice", aliases=["alice"]),
        projects=projects,
        selection_candidates=projects,
        skills=[skill for project in projects for skill in project.languages],
        til=[item for project in projects for item in project.til],
        my_commits=sum(project.my_commits for project in projects),
        selection_reason="기록과 결과가 함께 있는 프로젝트를 우선했습니다.",
    )


def _job(kind: str) -> JobRequest:
    return JobRequest(
        job_id="job-render",
        user_id="user",
        job_type=kind,
        document=DocumentSpec(
            kind=kind,
            profile_fields=ProfileFields(name="홍길동", contact_md="- me@example.com"),
        ),
    )


@pytest.mark.asyncio
async def test_portfolio_render_has_three_structured_cards_and_selection_reason() -> None:
    projects = [
        _project("acme/princess-secretary", [_til("page-1", "acme/princess-secretary")]),
        _project("acme/study-log"),
        _project("acme/third"),
    ]
    dropped = _project("acme/learning-log")
    proposal = await build_portfolio(
        _job("portfolio"),
        GitHubSnapshot(collected_at=NOW, complete=True, snapshot_digest="g" * 64),
        _evidence(projects).model_copy(update={"selection_candidates": [*projects, dropped]}),
    )

    body = proposal.body_markdown
    cards = body.split("## 프로젝트", 1)[1].split("## 🔍", 1)[0]
    assert len([line for line in cards.splitlines() if line.startswith("### ")]) == 3
    card_blocks = _card_blocks(cards)
    assert all(label in block for block in card_blocks for label in ("**개요**", "**목표**", "**성과**", "**성장**"))
    assert cards.count("#### ") <= 4
    assert "- 역할: 커밋 61개 (전체 61개 중 100%)" in cards
    assert f"{FILL_IN}" in cards
    assert "## 🔍 이 프로젝트를 고른 이유" in body
    assert "기록과 결과가 함께 있는 프로젝트를 우선했습니다." in body
    assert "~~learning-log~~" in body
    assert "학습 저장소 감점으로 제외" in body
    assert "---" in body
    assert not any(line.startswith("# ") for line in body.splitlines())


@pytest.mark.asyncio
async def test_resume_render_has_four_cards_and_intro_without_repository_name() -> None:
    projects = [_project(f"acme/project-{index}") for index in range(4)]
    proposal = await build_resume(
        _job("resume"),
        GitHubSnapshot(collected_at=NOW, complete=True, snapshot_digest="g" * 64),
        _evidence(projects),
    )

    body = proposal.body_markdown
    cards = body.split("## 주요 작업", 1)[1].split("## 📝", 1)[0]
    card_blocks = _card_blocks(cards)
    assert len(card_blocks) <= 4
    assert all(label in block for block in card_blocks for label in ("**문제**", "**목표**", "**기여**", "**성과**"))
    intro = body.split("## 소개", 1)[1].split("## 경력", 1)[0]
    assert "acme/project" not in intro
    assert intro.count("**") >= 8
    assert "## 🔍 이 프로젝트를 고른 이유" in body
    assert "---" in body
    assert not any(line.startswith("# ") for line in body.splitlines())


@pytest.mark.asyncio
async def test_resume_uses_profile_readme_sections_as_confirmable_drafts() -> None:
    project = _project("acme/project")
    snapshot = GitHubSnapshot(
        collected_at=NOW,
        complete=True,
        snapshot_digest="g" * 64,
        viewer_login="alice",
        repos=[
            RepoActivity(
                owner="acme",
                name="alice",
                readme=ReadmeBlob(
                    path="README.md",
                    blob_sha="wrong",
                    content="## 경력\n\n- 프로젝트 README를 초안으로 쓰면 안 됨",
                ),
            ),
            RepoActivity(
                owner="alice",
                name="alice",
                readme=ReadmeBlob(
                    path="README.md",
                    blob_sha="r",
                    content="## 경력\n\n- 백엔드 개발\n\n## 학력\n\n- 컴퓨터공학\n\n## 기술\n\n- Python",
                ),
            ),
        ],
    )
    proposal = await build_resume(_job("resume"), snapshot, _evidence([project]))

    assert "(자동 초안 — 확인해 주세요)" in proposal.body_markdown
    assert "- 백엔드 개발" in proposal.body_markdown
    assert "- 컴퓨터공학" in proposal.body_markdown
    assert "- Python" in proposal.body_markdown
    assert "프로젝트 README를 초안으로 쓰면 안 됨" not in proposal.body_markdown


@pytest.mark.asyncio
async def test_resume_takes_one_line_per_til_and_caps_each_slot() -> None:
    """`문제` 는 「문제 또는 목표」·「문제」·「원인」을 합친 값이다. 상한과 한 줄
    제한이 없으면 기록 네 건이 한 항목에 열두 줄로 눌려 들어간다."""
    til = [
        TilFact(
            id=f"til:{index}",
            date=date(2026, 8, index + 1),
            title=f"{index}일차",
            body_markdown="",
            page_id=f"p{index}",
            problem=f"대표 문제 {index}\n곁가지 원인 {index}\n더 깊은 원인 {index}",
            result=f"대표 결과 {index}\n곁가지 결과 {index}",
        )
        for index in range(4)
    ]
    resume = await build_resume(
        _job("resume"),
        GitHubSnapshot(collected_at=NOW, complete=True, snapshot_digest="g" * 64),
        _evidence([_project("acme/cache", til)]),
    )

    card = resume.body_markdown.split("## 주요 작업", 1)[1]
    problems = card.split("- **문제**", 1)[1].split("- **목표**", 1)[0]
    results = card.split("- **성과**", 1)[1]

    assert all(f"대표 문제 {index}" in problems for index in range(3))
    assert "대표 문제 3" not in problems
    assert "곁가지 원인 0" not in problems
    assert "곁가지 결과 0" not in results
    assert results.count("대표 결과") == 4


@pytest.mark.asyncio
async def test_empty_structured_slots_are_left_for_the_user_not_filled_from_commits() -> None:
    learned = TilFact(
        id="til:cache",
        date=date(2026, 8, 20),
        title="캐시 개선",
        body_markdown="응답 시간이 줄었다.\n배포 후 확인했다.",
        page_id="p1",
    )
    project = _project("acme/cache", [learned]).model_copy(
        update={
            "highlights": [
                CommitFact(
                    id="commit:acme/cache:aaaaaaaaaaaa",
                    repo="acme/cache",
                    sha="a" * 12,
                    subject="feat: 캐시 레이어",
                )
            ],
            "pull_requests": [
                WorkItem(
                    id="pr:acme/cache#1",
                    repo="acme/cache",
                    number=1,
                    title="feat: 캐시 PR",
                    source_type="pr",
                )
            ],
        }
    )
    empty = _project("acme/empty").model_copy(
        update={
            "highlights": [
                CommitFact(
                    id="commit:acme/empty:bbbbbbbbbbbb",
                    repo="acme/empty",
                    sha="b" * 12,
                    subject="feat: 결제 API 구현",
                )
            ]
        }
    )
    folio = await build_portfolio(
        _job("portfolio"),
        GitHubSnapshot(collected_at=NOW, complete=True, snapshot_digest="g" * 64),
        _evidence([project, empty, _project("acme/third")]),
    )
    resume = await build_resume(
        _job("resume"),
        GitHubSnapshot(collected_at=NOW, complete=True, snapshot_digest="g" * 64),
        _evidence([empty]),
    )

    folio_cards = folio.body_markdown.split("## 프로젝트", 1)[1]
    cache_card = folio_cards.split("### 1.", 1)[1].split("### 2.", 1)[0]
    empty_card = folio_cards.split("### 2.", 1)[1].split("### 3.", 1)[0]
    assert "캐시 개선" in cache_card.split("**성장**", 1)[1]
    # TIL 제목까지만. 본문도, 커밋 제목도 문서에 옮기지 않는다.
    assert "응답 시간이 줄었다." not in folio.body_markdown
    assert "feat: 결제 API 구현" not in folio.body_markdown
    assert "아직 비어 있습니다" in empty_card.split("**성과**", 1)[1]
    assert "feat: 결제 API 구현" not in resume.body_markdown
    assert "아직 비어 있습니다" in resume.body_markdown.split("**성과**", 1)[1]


def _card_blocks(section: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    for line in section.splitlines():
        if line.startswith("### ") and not line.startswith("#### "):
            if current:
                blocks.append("\n".join(current))
            current = [line]
        elif current:
            current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks
