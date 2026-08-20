"""Portfolio build writes a usable document, not a commit dump."""

from __future__ import annotations

from datetime import datetime, timezone

from app.contracts import (
    CommitFact,
    DocumentSpec,
    Evidence,
    GitHubSnapshot,
    JobRequest,
    ProfileFields,
    ProjectFacts,
    SkillFact,
    ViewerIdentity,
)
from app.pipelines.portfolio import build as portfolio_build
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
):
    chosen = projects if projects is not None else four_projects()
    return await portfolio_build(
        job(), snapshot(), evidence if evidence is not None else evidence_of(chosen), llm=llm
    )


def project_headings(body: str) -> list[str]:
    start = body.find("## 프로젝트")
    section = body[start:] if start >= 0 else ""
    return [line for line in section.splitlines() if line.startswith("### ")]


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


async def test_grounded_project_lines_keep_description_and_highlights() -> None:
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
    assert "> alpha 서비스" in body
    assert "- one" in body and "- two" in body and "- three" in body
    assert "- four" not in body
    assert "aaaaaaa" not in body
    assert "eeeeeee" not in body
    assert "fffffff" not in body
    commit_ids = {
        ref.source_id
        for ref in proposal.evidence_refs
        if ref.field == "projects_md" and ref.source_type == "commit"
    }
    assert f"commit:{repo}:{'a' * 12}" in commit_ids
    assert f"commit:{repo}:{'g' * 12}" not in commit_ids


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
    assert "feat: 알림 발송" in proposal.body_markdown
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

    assert "100%" not in body
    assert "- **Languages**: Python" in body
    assert "- **Frameworks**: FastAPI" in body
    assert "Redis" not in body
    assert "**Database**" not in body
    assert "— alpha" not in body
    assert "hackathon" not in body
    cards = body[body.find("## 프로젝트") :]
    alpha = cards.split("### ")[1]
    assert "기술: Python, FastAPI" in alpha
    assert "기술: Python, FastAPI" not in cards.split("### beta", 1)[-1]
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
