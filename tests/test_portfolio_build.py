"""Portfolio build writes a usable document, not a commit dump."""

from __future__ import annotations

import time
from datetime import datetime, timezone

from datetime import date

from app.contracts import (
    CommitFact,
    DocumentSpec,
    Evidence,
    GitHubSnapshot,
    JobRequest,
    ProfileFields,
    ProjectFacts,
    SkillFact,
    TilFact,
    ViewerIdentity,
    WorkItem,
)
from app.pipelines.portfolio import briefs
from app.pipelines.portfolio import build as portfolio_build
from app.pipelines.portfolio import team
from tests.test_llm_guard import FakeLLM

NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)
FIELDS = ProfileFields(name="홍길동", contact_md="- me@example.com")


def _highlight(repo: str, sha: str, subject: str) -> CommitFact:
    return CommitFact(id=f"commit:{repo}:{sha}", repo=repo, sha=sha, subject=subject)


def _project(
    name: str,
    *,
    sha: str,
    subject: str,
    commits: int,
    owner: str = "acme",
    highlights: list[CommitFact] | None = None,
) -> ProjectFacts:
    repo = f"{owner}/{name}"
    return ProjectFacts(
        id=f"repo:{repo}",
        repo=repo,
        url=f"https://github.com/{repo}",
        description=f"{name} 서비스",
        my_commits=commits,
        total_commits=commits,
        highlights=highlights or [_highlight(repo, sha, subject)],
        languages=[
            SkillFact(
                id=f"skill:python:{repo}",
                name="Python",
                category="language",
                weight=1.0,
                measured=True,
                repos=[repo],
            )
        ],
        score=float(commits),
    )


def four_projects() -> list[ProjectFacts]:
    return [
        _project("alpha", sha="a" * 12, subject="feat: 결제 API 구현", commits=20),
        _project("beta", sha="b" * 12, subject="feat: 알림 발송", commits=12),
        _project("gamma", sha="c" * 12, subject="fix: 타임아웃 수정", commits=8),
        _project("delta", sha="d" * 12, subject="chore: 설정 정리", commits=2),
    ]


def evidence_of(
    projects: list[ProjectFacts],
    *,
    skills: list[SkillFact] | None = None,
    period: bool = True,
) -> Evidence:
    return Evidence(
        viewer=ViewerIdentity(login="alice", aliases=["alice"]),
        projects=projects,
        skills=skills if skills is not None else _skills_from(projects),
        period_start=NOW if period else None,
        period_end=NOW if period else None,
        my_commits=sum(p.my_commits for p in projects),
        complete=True,
    )


def _skills_from(projects: list[ProjectFacts]) -> list[SkillFact]:
    merged: dict[str, SkillFact] = {}
    for project in projects:
        for skill in project.languages:
            current = merged.get(skill.name)
            if current is None:
                merged[skill.name] = skill.model_copy(update={"repos": list(skill.repos)})
                continue
            repos = list(current.repos)
            repos.extend(repo for repo in skill.repos if repo not in repos)
            merged[skill.name] = current.model_copy(update={"repos": repos})
    return list(merged.values())


def snapshot() -> GitHubSnapshot:
    return GitHubSnapshot(
        collected_at=NOW,
        complete=True,
        snapshot_digest="d" * 64,
        viewer_login="alice",
    )


def job() -> JobRequest:
    return JobRequest(
        job_id="j1",
        user_id="u1",
        job_type="portfolio",
        document=DocumentSpec(kind="portfolio", profile_fields=FIELDS),
    )


async def build_portfolio(
    llm=None,
    projects: list[ProjectFacts] | None = None,
    evidence: Evidence | None = None,
    deadline: float | None = None,
):
    chosen = projects if projects is not None else four_projects()
    return await portfolio_build(
        job(),
        snapshot(),
        evidence if evidence is not None else evidence_of(chosen),
        llm=llm,
        deadline=deadline,
    )


def project_headings(body: str) -> list[str]:
    start = body.find("## 프로젝트")
    section = body[start:] if start >= 0 else ""
    headings = []
    for line in section.splitlines():
        if not line.startswith("### "):
            continue
        value = line[4:]
        first, _, rest = value.partition(" ")
        if first.endswith(".") and first[:-1].isdigit():
            value = rest
        headings.append("### " + value.split(" —", 1)[0])
    return headings


def grounded_llm() -> FakeLLM:
    return FakeLLM(
        {
            "intro": [
                {
                    "text": "결제와 알림을 다루는 백엔드를 만들었습니다.",
                    "evidence_ids": ["repo:acme/alpha"],
                }
            ]
        }
    )


async def test_portfolio_keeps_only_the_top_three_projects() -> None:
    proposal = await build_portfolio(llm=None)

    assert project_headings(proposal.body_markdown) == [
        "### alpha",
        "### beta",
        "### gamma",
    ]
    assert "### delta" not in proposal.body_markdown
    assert "### Contact" not in project_headings(proposal.body_markdown)
    assert "Activity" not in proposal.body_markdown
    assert "규모:" not in proposal.body_markdown


async def test_colliding_repo_names_use_full_path() -> None:
    projects = [
        _project("alpha", sha="a" * 12, subject="feat: 하나", commits=20),
        _project("alpha", sha="b" * 12, subject="feat: 둘", commits=12, owner="other"),
        _project("beta", sha="c" * 12, subject="feat: 셋", commits=8),
        _project("alpha", sha="d" * 12, subject="feat: 넷", commits=2, owner="foo"),
    ]
    proposal = await build_portfolio(llm=None, projects=projects)

    assert project_headings(proposal.body_markdown) == [
        "### acme/alpha",
        "### other/alpha",
        "### beta",
    ]
    assert "### foo/alpha" not in proposal.body_markdown


async def test_grounded_project_lines_keep_description_and_commit_refs() -> None:
    repo = "acme/alpha"
    alpha = _project(
        "alpha",
        sha="a" * 12,
        subject="one",
        commits=20,
        highlights=[
            _highlight(repo, "a" * 12, "one"),
            _highlight(repo, "e" * 12, "two"),
            _highlight(repo, "f" * 12, "three"),
            _highlight(repo, "g" * 12, "four"),
        ],
    )
    projects = [
        alpha,
        _project("beta", sha="b" * 12, subject="feat: 알림 발송", commits=12),
        _project("gamma", sha="c" * 12, subject="fix: 타임아웃 수정", commits=8),
        _project("delta", sha="d" * 12, subject="chore: 설정 정리", commits=2),
    ]
    proposal = await build_portfolio(grounded_llm(), projects)

    body = proposal.body_markdown
    assert "결제와 알림을 다루는 백엔드를 만들었습니다." in body
    assert "결제 API를 구현했습니다." not in body
    assert "— alpha 서비스" in body
    work = body.split("**성과**", 1)[1].split("**성장**", 1)[0]
    # 커밋 제목은 성과가 아니다. TIL 이 없으면 사용자가 채울 빈 칸으로 남는다.
    assert "아직 비어 있습니다" in work
    assert "- one" not in work and "- two" not in work
    assert "aaaaaaa" not in body
    assert "eeeeeee" not in body
    assert "fffffff" not in body
    commit_ids = {
        ref.source_id
        for ref in proposal.evidence_refs
        if ref.field == "projects_md" and ref.source_type == "commit"
    }
    assert f"commit:{repo}:{'a' * 12}" in commit_ids
    assert f"commit:{repo}:{'g' * 12}" in commit_ids


async def test_hallucinated_project_ids_fall_back_to_facts() -> None:
    llm = FakeLLM(
        {
            "intro": [],
            "projects": [
                {
                    "text": "쿠버네티스 클러스터를 운영했습니다.",
                    "evidence_ids": ["repo:made/up"],
                },
                {
                    "text": "결제 API 기여를 했습니다.",
                    "evidence_ids": ["repo:acme/alpha"],
                },
            ],
        }
    )
    proposal = await build_portfolio(llm)

    assert "쿠버네티스" not in proposal.body_markdown
    assert "결제 API 기여를 했습니다." not in proposal.body_markdown
    assert "aaaaaaa" not in proposal.body_markdown
    assert "feat: 알림 발송" not in proposal.body_markdown
    assert "아직 비어 있습니다" in proposal.body_markdown.split("**성과**", 1)[1]
    assert "bbbbbbb" not in proposal.body_markdown


async def test_skills_list_featured_repos_not_percents() -> None:
    projects = four_projects()
    skills = [
        SkillFact(
            id="skill:python",
            name="Python",
            category="language",
            weight=1.0,
            measured=True,
            repos=["acme/alpha", "acme/beta", "acme/gamma", "acme/delta"],
        ),
        SkillFact(
            id="skill:fastapi",
            name="FastAPI",
            category="framework",
            weight=0.8,
            repos=["acme/alpha"],
        ),
        SkillFact(
            id="skill:redis",
            name="Redis",
            category="database",
            weight=0.5,
            repos=["acme/delta"],
        ),
    ]
    proposal = await build_portfolio(
        llm=None, projects=projects, evidence=evidence_of(projects, skills=skills)
    )
    body = proposal.body_markdown

    skills_section = body.split("## 기술", 1)[1].split("## 프로젝트", 1)[0]
    assert "100%" not in skills_section
    assert "- **Languages**: Python" in body
    assert "- **Frameworks**: FastAPI" in body
    assert "Redis" not in body
    assert "**Database**" not in body
    assert "### 1. alpha — alpha 서비스" in body
    assert "hackathon" not in body
    cards = body[body.find("## 프로젝트") :]
    alpha = cards.split("### ")[1]
    assert "기술 스택: Python, FastAPI" in alpha
    assert "기술 스택: Python, FastAPI" not in cards.split("### 2. beta", 1)[-1]
    skill_repos = {
        ref.repo
        for ref in proposal.evidence_refs
        if ref.field == "projects_md" and ref.source_id == "skill:fastapi"
    }
    assert skill_repos == {"acme/alpha"}


async def test_fallback_about_does_not_invent_a_sentence() -> None:
    proposal = await build_portfolio(llm=None)
    assert "주로 사용하는 기술은" not in proposal.body_markdown
    assert "동안 프로젝트" not in proposal.body_markdown
    assert "## 소개" not in proposal.body_markdown


async def test_about_stays_empty_when_there_are_no_skills() -> None:
    projects = four_projects()
    proposal = await build_portfolio(
        llm=None, projects=projects, evidence=evidence_of(projects, skills=[])
    )
    assert "동안 프로젝트" not in proposal.body_markdown
    assert "## 소개" not in proposal.body_markdown


async def test_intro_refs_cite_only_used_evidence() -> None:
    llm = FakeLLM(
        {
            "intro": [
                {
                    "text": "결제 도메인 백엔드를 맡았습니다.",
                    "evidence_ids": ["repo:acme/alpha"],
                }
            ],
            "projects": [],
        }
    )
    proposal = await build_portfolio(llm)

    summary_ids = {
        ref.source_id for ref in proposal.evidence_refs if ref.field == "summary_md"
    }
    assert summary_ids == {"repo:acme/alpha"}


def _empty_pitch() -> dict:
    return {"pitch": []}


def _empty_work() -> dict:
    return {"work_ids": []}


def _slot_llm(
    *,
    selected: list[str],
    intro: list[dict] | None = None,
    pitches: list[dict] | None = None,
    works: list[dict] | None = None,
) -> FakeLLM:
    payloads: list = [{"selected_ids": selected, "reason": "test"}]
    for index, _project_id in enumerate(selected):
        payloads.append(pitches[index] if pitches and index < len(pitches) else _empty_pitch())
        payloads.append(works[index] if works and index < len(works) else _empty_work())
    payloads.append(
        {
            "intro": intro
            or [
                {
                    "text": "결제와 알림을 다루는 백엔드를 만들었습니다.",
                    "evidence_ids": ["repo:acme/alpha"],
                }
            ]
        }
    )
    return FakeLLM(payloads)


async def test_curator_order_replaces_score_order() -> None:
    llm = _slot_llm(selected=["repo:acme/delta", "repo:acme/alpha", "repo:acme/beta"])
    proposal = await build_portfolio(llm)

    assert project_headings(proposal.body_markdown) == [
        "### delta",
        "### alpha",
        "### beta",
    ]
    assert "### gamma" not in proposal.body_markdown
    assert "cards" not in proposal.model_dump()
    assert "selected_ids" not in proposal.model_dump()
    assert "> test" in proposal.body_markdown


async def test_curator_may_keep_two_projects() -> None:
    llm = _slot_llm(selected=["repo:acme/alpha", "repo:acme/gamma"])
    proposal = await build_portfolio(llm)
    assert project_headings(proposal.body_markdown) == ["### alpha", "### gamma"]


async def test_fake_ids_are_dropped_and_filled_by_score() -> None:
    llm = _slot_llm(selected=["repo:made/up", "repo:acme/delta"])
    proposal = await build_portfolio(llm)
    assert project_headings(proposal.body_markdown)[0] == "### delta"
    assert "### made" not in proposal.body_markdown
    assert len(project_headings(proposal.body_markdown)) == 3


async def test_q1_pitch_and_q2_ids_use_original_titles() -> None:
    sha = "a" * 12
    commit_id = f"commit:acme/alpha:{sha}"
    alpha = _project(
        "alpha",
        sha=sha,
        subject="one",
        commits=20,
        highlights=[_highlight("acme/alpha", sha, "one")],
    )
    llm = _slot_llm(
        selected=["repo:acme/alpha", "repo:acme/beta"],
        pitches=[
            {
                "pitch": [
                    {"text": "결제 서비스를 만들었습니다.", "evidence_ids": ["repo:acme/alpha"]}
                ]
            }
        ],
        works=[{"work_ids": [commit_id], "text": "결제 검증을 붙였습니다."}],
        intro=[],
    )
    proposal = await build_portfolio(llm, [alpha, *_project_tail()])
    body = proposal.body_markdown
    assert "결제 서비스를 만들었습니다." in body
    assert "결제 검증을 붙였습니다." not in body
    assert "- one" not in body
    assert "아직 비어 있습니다" in body.split("**성과**", 1)[1]


async def test_repo_only_work_ids_never_put_commit_titles_in_achievements() -> None:
    llm = _slot_llm(
        selected=["repo:acme/alpha", "repo:acme/beta", "repo:acme/gamma"],
        works=[{"work_ids": ["repo:acme/alpha"]}],
        intro=[],
    )
    proposal = await build_portfolio(llm)
    assert "쿠버네티스" not in proposal.body_markdown
    assert "feat: 결제 API 구현" not in proposal.body_markdown
    assert "아직 비어 있습니다" in proposal.body_markdown.split("**성과**", 1)[1]


async def test_one_fill_error_falls_back_that_card() -> None:
    llm = FakeLLM(
        [
            {
                "selected_ids": ["repo:acme/alpha", "repo:acme/beta", "repo:acme/gamma"],
                "reason": "",
            },
            RuntimeError("q1 exploded"),
            _empty_work(),
            _empty_pitch(),
            _empty_work(),
            _empty_pitch(),
            _empty_work(),
            {
                "intro": [
                    {
                        "text": "결제와 알림을 다루는 백엔드를 만들었습니다.",
                        "evidence_ids": ["repo:acme/alpha"],
                    }
                ]
            },
        ]
    )
    proposal = await build_portfolio(llm)
    assert proposal.status == "proposed"
    assert "— alpha 서비스" in proposal.body_markdown
    assert "feat: 결제 API 구현" not in proposal.body_markdown
    assert "아직 비어 있습니다" in proposal.body_markdown.split("**성과**", 1)[1]


async def test_unselected_repos_are_not_filled() -> None:
    llm = _slot_llm(selected=["repo:acme/alpha", "repo:acme/beta", "repo:acme/gamma"])
    proposal = await build_portfolio(llm)
    assert "### delta" not in proposal.body_markdown
    assert llm.schemas.count("_SelectDraft") == 1
    assert llm.schemas.count("_PitchDraft") == 3
    assert llm.schemas.count("_WorkDraft") == 3
    assert llm.schemas.count("_WriteDraft") == 1
    fill_prompts = [
        prompt
        for prompt, name in zip(llm.prompts, llm.schemas)
        if name in {"_PitchDraft", "_WorkDraft"}
    ]
    assert all("delta 서비스" not in prompt for prompt in fill_prompts)


async def test_fill_and_intro_use_sheet_not_split_json() -> None:
    llm = _slot_llm(selected=["repo:acme/alpha", "repo:acme/beta"])
    await build_portfolio(llm)
    fill_prompts = [
        prompt
        for prompt, name in zip(llm.prompts, llm.schemas)
        if name in {"_PitchDraft", "_WorkDraft"}
    ]
    assert fill_prompts
    assert all('"sheet"' in prompt for prompt in fill_prompts)
    assert all('"highlights"' not in prompt for prompt in fill_prompts)
    assert all("delta 서비스" not in prompt for prompt in fill_prompts)
    intro = next(prompt for prompt, name in zip(llm.prompts, llm.schemas) if name == "_WriteDraft")
    assert '"sheet"' in intro
    assert "delta 서비스" not in intro
    assert '"highlights"' not in intro


async def test_intro_sees_filled_pitch_not_unselected_repo() -> None:
    llm = _slot_llm(
        selected=["repo:acme/alpha", "repo:acme/beta"],
        pitches=[
            {
                "pitch": [
                    {"text": "알파 결제를 만들었습니다.", "evidence_ids": ["repo:acme/alpha"]}
                ]
            }
        ],
    )
    await build_portfolio(llm)
    intro = next(prompt for prompt, name in zip(llm.prompts, llm.schemas) if name == "_WriteDraft")
    assert "알파 결제를 만들었습니다." in intro
    assert "delta 서비스" not in intro


async def test_expired_deadline_keeps_score_order() -> None:
    llm = _slot_llm(selected=["repo:acme/delta", "repo:acme/alpha", "repo:acme/beta"])
    proposal = await build_portfolio(llm, deadline=time.monotonic())
    assert project_headings(proposal.body_markdown) == [
        "### alpha",
        "### beta",
        "### gamma",
    ]


def _project_tail() -> list[ProjectFacts]:
    return [
        _project("beta", sha="b" * 12, subject="feat: 알림 발송", commits=12),
        _project("gamma", sha="c" * 12, subject="fix: 타임아웃 수정", commits=8),
        _project("delta", sha="d" * 12, subject="chore: 설정 정리", commits=2),
    ]


async def test_first_pass_briefs_cover_every_candidate_not_the_portfolio() -> None:
    proposal = await build_portfolio(llm=None)
    sheets = proposal._publish_briefs
    titles = [item["title"] for item in sheets]

    assert titles == ["alpha", "beta", "gamma", "delta"]
    assert "### delta" not in proposal.body_markdown
    assert any("## 커밋" in item["markdown"] for item in sheets)
    assert all("날짜:" not in item["markdown"] for item in sheets)
    assert all("## 소개" not in item["markdown"] for item in sheets)
    dumped = proposal.model_dump()
    assert "publish_briefs" not in dumped
    assert "_publish_briefs" not in dumped


def test_brief_renderer_keeps_original_work_titles() -> None:
    evidence = evidence_of(four_projects())
    sheets = briefs.render_briefs(evidence)
    alpha = next(item for item in sheets if item["title"] == "alpha")
    assert "feat: 결제 API 구현" in alpha["markdown"]
    assert "날짜:" not in alpha["markdown"]


def test_brief_learned_includes_two_body_lines_without_date_marker() -> None:
    learned = TilFact(
        id="til:cache",
        date=date(2026, 8, 20),
        title="캐시 개선",
        body_markdown=(
            "# 메모\n\n날짜: 2026-08-20\n\n응답 시간이 줄었다.\n배포 후 확인했다.\n세 번째는 버린다.\n"
        ),
        page_id="p1",
    )
    project = four_projects()[0].model_copy(update={"til": [learned]})
    sheet = briefs.brief_of(project, evidence_of([project]))
    assert "캐시 개선" in sheet
    assert "응답 시간이 줄었다." in sheet
    assert "배포 후 확인했다." in sheet
    assert "세 번째는 버린다." not in sheet
    assert "날짜:" not in sheet


async def test_portfolio_card_keeps_til_title_not_body() -> None:
    learned = TilFact(
        id="til:cache",
        date=date(2026, 8, 20),
        title="캐시 개선",
        body_markdown="응답 시간이 줄었다.",
        page_id="p1",
    )
    project = four_projects()[0].model_copy(update={"til": [learned]})
    proposal = await build_portfolio(llm=None, projects=[project, *_project_tail()[:2]])
    growth = proposal.body_markdown.split("**성장**", 1)[1]
    assert "캐시 개선" in growth
    # 본문은 어느 칸에도 옮기지 않는다. 제목만 근거로 남긴다.
    assert "응답 시간이 줄었다." not in proposal.body_markdown


async def test_unmatched_til_is_hub_tail_not_portfolio() -> None:
    extra = TilFact(
        id="til:solo",
        date=date(2026, 8, 1),
        title="혼자 있는 기록",
        body_markdown="",
        page_id="x",
    )
    proposal = await build_portfolio(
        llm=None,
        evidence=evidence_of(four_projects()).model_copy(update={"unmatched_til": [extra]}),
    )
    assert "혼자 있는 기록" not in proposal.body_markdown
    assert "그 외 학습 기록" not in proposal.body_markdown
    assert "혼자 있는 기록" in proposal._hub_tail
    assert "## 그 외 학습" in proposal._hub_tail
    assert "날짜:" not in proposal._hub_tail
    dumped = proposal.model_dump()
    assert "_hub_tail" not in dumped
    assert "hub_tail" not in dumped


def test_default_work_ids_mix_and_dedupe_titles() -> None:
    repo = "acme/alpha"
    project = ProjectFacts(
        id=f"repo:{repo}",
        repo=repo,
        highlights=[
            _highlight(repo, "a" * 12, "feat: 결제 API 구현"),
            _highlight(repo, "b" * 12, "feat: 결제 API 구현"),
            _highlight(repo, "c" * 12, "feat: 다른 커밋"),
        ],
        pull_requests=[
            WorkItem(
                id="pr:acme/alpha#1",
                repo=repo,
                number=1,
                title="feat: 결제 검증",
                source_type="pr",
            )
        ],
        til=[
            TilFact(
                id="til:learn",
                date=date(2026, 8, 1),
                title="캐시 개선",
                body_markdown="",
                page_id="t",
            )
        ],
    )
    assert team.default_work_ids(project) == [
        f"commit:{repo}:{'a' * 12}",
        "pr:acme/alpha#1",
        "til:learn",
    ]
    assert team.sanitize_work_ids(
        [f"commit:{repo}:{'a' * 12}", f"commit:{repo}:{'b' * 12}", "pr:acme/alpha#1"],
        project,
    ) == [f"commit:{repo}:{'a' * 12}", "pr:acme/alpha#1"]


async def test_llm_off_work_mixes_commit_pr_and_til() -> None:
    repo = "acme/alpha"
    alpha = _project(
        "alpha",
        sha="a" * 12,
        subject="feat: 결제 API 구현",
        commits=20,
        highlights=[
            _highlight(repo, "a" * 12, "feat: 결제 API 구현"),
            _highlight(repo, "b" * 12, "feat: 두 번째 커밋"),
            _highlight(repo, "c" * 12, "feat: 세 번째 커밋"),
        ],
    ).model_copy(
        update={
            "pull_requests": [
                WorkItem(
                    id="pr:acme/alpha#1",
                    repo=repo,
                    number=1,
                    title="feat: 결제 검증",
                    source_type="pr",
                )
            ],
            "til": [
                TilFact(
                    id="til:learn",
                    date=date(2026, 8, 1),
                    title="캐시 개선",
                    body_markdown="",
                    page_id="t",
                )
            ],
        }
    )
    proposal = await build_portfolio(llm=None, projects=[alpha, *_project_tail()[:2]])
    # 성과는 TIL 의 시도·결과에서만 나온다. 커밋 제목을 성과로 올리면 사용자가 쓰지
    # 않은 문장이 성과가 되고, 커밋이 많은 저장소일수록 카드가 부풀어 오른다.
    work = proposal.body_markdown.split("**성과**", 1)[1].split("**성장**", 1)[0]
    assert "캐시 개선" in work
    assert "feat: 결제 API 구현" not in work
    assert "두 번째 커밋" not in work
    assert "세 번째 커밋" not in work
    # 커밋과 PR 은 개요의 기여 수치로 남는다.
    assert "- 역할: 커밋 20개" in proposal.body_markdown
