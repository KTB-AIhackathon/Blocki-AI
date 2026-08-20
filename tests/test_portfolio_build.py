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


def _project(name: str, *, sha: str, subject: str, commits: int) -> ProjectFacts:
    repo = f"acme/{name}"
    return ProjectFacts(
        id=f"repo:{repo}",
        repo=repo,
        url=f"https://github.com/{repo}",
        description=f"{name} 서비스",
        my_commits=commits,
        total_commits=commits,
        highlights=[_highlight(repo, sha, subject)],
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


def evidence_of(projects: list[ProjectFacts]) -> Evidence:
    return Evidence(
        viewer=ViewerIdentity(login="alice", aliases=["alice"]),
        projects=projects,
        skills=projects[0].languages,
        period_start=NOW,
        period_end=NOW,
        my_commits=sum(p.my_commits for p in projects),
        complete=True,
    )


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


async def build_portfolio(llm=None, projects: list[ProjectFacts] | None = None):
    chosen = projects if projects is not None else four_projects()
    return await portfolio_build(job(), snapshot(), evidence_of(chosen), llm=llm)


def headings(body: str) -> list[str]:
    return [line for line in body.splitlines() if line.startswith("### acme/")]


async def test_portfolio_keeps_only_the_top_three_projects() -> None:
    proposal = await build_portfolio(llm=None)

    assert headings(proposal.body_markdown) == [
        "### acme/alpha",
        "### acme/beta",
        "### acme/gamma",
    ]
    assert "### acme/delta" not in proposal.body_markdown
    assert "| 프로젝트 | 3개 |" in proposal.body_markdown


async def test_grounded_project_lines_replace_sha_dumps() -> None:
    llm = FakeLLM(
        {
            "intro": [
                {
                    "text": "결제와 알림을 다루는 백엔드를 만들었습니다.",
                    "evidence_ids": ["repo:acme/alpha"],
                }
            ],
            "projects": [
                {
                    "text": "결제 API를 구현했습니다.",
                    "evidence_ids": ["repo:acme/alpha"],
                },
                {
                    "text": "알림 발송을 추가했습니다.",
                    "evidence_ids": ["repo:acme/beta"],
                },
                {
                    "text": "타임아웃을 수정했습니다.",
                    "evidence_ids": ["repo:acme/gamma"],
                },
            ],
        }
    )
    proposal = await build_portfolio(llm)

    assert "결제와 알림을 다루는 백엔드를 만들었습니다." in proposal.body_markdown
    assert "결제 API를 구현했습니다." in proposal.body_markdown
    assert "알림 발송을 추가했습니다." in proposal.body_markdown
    assert "타임아웃을 수정했습니다." in proposal.body_markdown
    assert "aaaaaaa" not in proposal.body_markdown
    assert "bbbbbbb" not in proposal.body_markdown
    assert "ccccccc" not in proposal.body_markdown
    assert "### acme/delta" not in proposal.body_markdown


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
    assert "결제 API 기여를 했습니다." in proposal.body_markdown
    assert "aaaaaaa" not in proposal.body_markdown
    assert "feat: 알림 발송" in proposal.body_markdown
    assert "bbbbbbb" in proposal.body_markdown


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
