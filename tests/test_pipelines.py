from __future__ import annotations

import pytest

from app import pipelines, render
from app.collect.github import collect_github
from app.pipelines import common
from app.contracts import (
    CollectRequest,
    DocumentSpec,
    JobRequest,
    ProfileFields,
    ReadmeTarget,
    RepoRef,
)
from tests.conftest import PAT, FakeGitHub, commit

FIELDS = ProfileFields(
    name="홍길동",
    contact_md="- 이메일: me@example.com",
    experience_md="- 2025 ~ : 백엔드 엔지니어",
    education_md="- 2020 ~ 2024: 컴퓨터공학",
)


async def snapshot_for(job_type: str, fake: FakeGitHub, **kwargs):
    pipeline = pipelines.resolve(job_type)
    assert pipeline is not None
    return await collect_github(
        CollectRequest(
            job_id="j1",
            repos=[RepoRef(owner="acme", name="demo")],
            policy=pipeline.policy,
            **kwargs,
        ),
        PAT,
        call_tool=fake,
    )


def document_job(job_type: str, fields: ProfileFields = FIELDS) -> JobRequest:
    return JobRequest(
        job_id="j1",
        user_id="u1",
        job_type=job_type,
        document=DocumentSpec(kind=job_type, profile_fields=fields),
    )


@pytest.mark.parametrize("job_type", ["portfolio", "resume"])
async def test_document_pipeline_renders_grounded_sections(job_type: str) -> None:
    fake = FakeGitHub(
        list_commits=[
            commit("a" * 12, "feat: 결제 API 구현", days=10),
            commit("b" * 12, "perf: 응답 캐시로 지연 단축", days=4),
        ]
    )
    snapshot = await snapshot_for(job_type, fake)
    proposal = await pipelines.run(document_job(job_type), snapshot)

    body = proposal.body_markdown
    assert proposal.status == ("partial" if job_type == "resume" else "proposed")
    assert proposal.kind == job_type
    assert "홍길동" == proposal.owner_name
    assert "홍길동" not in body
    if job_type == "portfolio":
        assert "Python" in body and "FastAPI" in body
    else:
        assert common.FILL_IN in body
    # 커밋 제목은 문서에 싣지 않는다. 근거 참조로만 남는다.
    assert "결제 API 구현" not in body
    assert "demo" in body
    assert "aaaaaaa" not in body
    assert "pyproject.toml" not in body
    assert PAT not in body
    assert proposal.template_ref is not None
    assert proposal.template_ref.sha256 == render.template_ref(job_type, "v1").sha256
    assert proposal.evidence_refs
    assert proposal.proposal_digest


@pytest.mark.parametrize("job_type", ["portfolio", "resume"])
async def test_document_pipeline_cites_only_real_evidence(job_type: str) -> None:
    fake = FakeGitHub()
    snapshot = await snapshot_for(job_type, fake)
    proposal = await pipelines.run(document_job(job_type), snapshot)

    from app.analyze import analyze

    allowed = analyze(snapshot).ids()
    cited = {ref.source_id for ref in proposal.evidence_refs}
    assert cited
    assert cited <= allowed


async def test_resume_without_a_career_leaves_a_blank_to_fill_in() -> None:
    fake = FakeGitHub()
    snapshot = await snapshot_for("resume", fake)
    job = document_job("resume", ProfileFields(name="홍길동"))
    proposal = await pipelines.run(job, snapshot)

    assert proposal.status == "partial"
    assert set(proposal.unresolved_fields) >= {"experience_md", "education_md"}
    assert proposal.error is None
    assert "## 경력" in proposal.body_markdown
    assert "## 학력" in proposal.body_markdown
    assert proposal.body_markdown.count(common.FILL_IN) == 4


async def test_a_supplied_career_replaces_the_blank() -> None:
    fake = FakeGitHub()
    snapshot = await snapshot_for("resume", fake)
    job = document_job(
        "resume",
        ProfileFields(name="홍길동", experience_md="- 2025 ~ : 백엔드", education_md=""),
    )
    proposal = await pipelines.run(job, snapshot)

    assert "- 2025 ~ : 백엔드" in proposal.body_markdown
    assert proposal.body_markdown.count(common.FILL_IN) == 3
    assert "education_md" in proposal.unresolved_fields
    assert "experience_md" not in proposal.unresolved_fields


async def test_a_document_without_a_name_is_still_blocked() -> None:
    fake = FakeGitHub()
    snapshot = await snapshot_for("resume", fake)
    job = document_job("resume", ProfileFields(name=" "))
    proposal = await pipelines.run(job, snapshot)

    assert proposal.status == "blocked"
    assert proposal.unresolved_fields == ["name"]
    assert proposal.error is not None and proposal.error.code == "blocked"
    assert proposal.body_markdown == ""


async def test_blank_contact_renders_fill_in_and_remains_partial() -> None:
    fake = FakeGitHub()
    snapshot = await snapshot_for("portfolio", fake)
    proposal = await pipelines.run(
        document_job("portfolio", ProfileFields(name="홍길동")), snapshot
    )

    assert proposal.status == "partial"
    assert "contact_md" in proposal.unresolved_fields
    assert common.FILL_IN in proposal.body_markdown


async def test_portfolio_does_not_require_experience() -> None:
    fake = FakeGitHub()
    snapshot = await snapshot_for("portfolio", fake)
    job = document_job("portfolio", ProfileFields(name="홍길동", contact_md="- me@a.com"))
    proposal = await pipelines.run(job, snapshot)

    assert proposal.status == "proposed"
    assert "Experience" not in proposal.body_markdown
    assert "Education" not in proposal.body_markdown


async def test_portfolio_and_resume_differ_in_shape() -> None:
    fake = FakeGitHub(list_commits=[commit("a" * 12, "feat: 결제 API 구현")])
    portfolio = await pipelines.run(
        document_job("portfolio"), await snapshot_for("portfolio", fake)
    )
    resume = await pipelines.run(document_job("resume"), await snapshot_for("resume", FakeGitHub()))

    assert "## 프로젝트" in portfolio.body_markdown
    assert "## 주요 작업" in resume.body_markdown
    assert "Activity" not in portfolio.body_markdown
    assert "Activity" not in resume.body_markdown
    assert "## 경력" in resume.body_markdown
    assert len(portfolio.body_markdown) != len(resume.body_markdown)


async def test_legacy_profile_document_job_type_is_normalised() -> None:
    job = JobRequest(
        job_id="j1",
        user_id="u1",
        job_type="profile_document",
        document=DocumentSpec(kind="resume", profile_fields=FIELDS),
    )
    assert job.job_type == "resume"
    proposal = await pipelines.run(job, await snapshot_for("resume", FakeGitHub()))
    assert proposal.kind == "resume"


async def test_progress_memo_groups_by_date() -> None:
    fake = FakeGitHub(
        list_commits=[
            commit("a" * 12, "feat: 하루 전", days=1),
            commit("b" * 12, "fix: 사흘 전", days=3),
        ]
    )
    snapshot = await snapshot_for("progress_summary", fake)
    job = JobRequest(job_id="j1", user_id="u1", job_type="progress_summary")
    proposal = await pipelines.run(job, snapshot)

    assert proposal.status == "proposed"
    assert proposal.body_markdown.startswith("# 진행 메모")
    assert proposal.body_markdown.count("## 2026-05-") == 2
    assert proposal.template_ref is None


async def test_progress_reports_no_change_when_nothing_new() -> None:
    fake = FakeGitHub(list_commits=[])
    snapshot = await snapshot_for("progress_summary", fake)
    job = JobRequest(job_id="j1", user_id="u1", job_type="progress_summary")
    proposal = await pipelines.run(job, snapshot)

    assert proposal.status == "no_change"
    assert proposal.body_markdown == ""


async def test_readme_pipeline_proposes_an_action() -> None:
    fake = FakeGitHub()
    job = JobRequest(
        job_id="j1",
        user_id="u1",
        job_type="readme_proposal",
        readme=ReadmeTarget(owner="acme", repo="demo", path="README.md"),
    )
    snapshot = await snapshot_for("readme_proposal", fake)
    proposal = await pipelines.run(job, snapshot)

    assert proposal.status == "proposed"
    assert proposal.proposed_action is not None
    assert proposal.proposed_action.path == "README.md"
    assert proposal.action_digest


def test_render_drops_sections_with_no_content() -> None:
    body = render.render(
        "portfolio",
        "v1",
        {"name": "홍길동", "summary_md": "한 줄 소개", "skills_md": "- Python"},
    )
    assert "## 소개" in body
    assert "## 기술" in body
    assert "## 프로젝트" not in body
    assert "### Contact" not in body
    assert "\n\n\n" not in body


def test_render_keeps_parent_heading_with_a_filled_child() -> None:
    pruned = render.prune_empty_sections("# 제목\n\n## 빈 섹션\n\n### 채워진 하위\n\n내용\n")
    assert "# 제목" in pruned
    assert "## 빈 섹션" in pruned
    assert "### 채워진 하위" in pruned


def test_portfolio_budget_is_200_seconds() -> None:
    portfolio = pipelines.resolve("portfolio")
    resume = pipelines.resolve("resume")
    assert portfolio is not None and resume is not None
    assert portfolio.timeout_seconds == 200
    assert portfolio.evidence is not None and portfolio.evidence.max_projects == 6
    assert resume.timeout_seconds == 90
    assert resume.evidence is not None and resume.evidence.max_projects == 4


def test_render_substitution_cannot_execute_template_syntax() -> None:
    body = render.render(
        "portfolio", "v1", {"name": "홍길동", "summary_md": "{{skills_md}} {% raw %}"}
    )
    assert "{{skills_md}}" in body
    assert "{% raw %}" in body
