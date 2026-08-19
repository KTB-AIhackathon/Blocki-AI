from datetime import datetime, timezone
from hashlib import sha256
from uuid import UUID

from app.artifacts import build_artifact
from app.contracts import (
    CommitSummary,
    DocumentSpec,
    GitHubSnapshot,
    JobRequest,
    LanguageShare,
    ProfileFields,
    ReadmeBlob,
    ReadmeTarget,
    RepoActivity,
)
from app.templates_render import template_path


def _snapshot(*, complete: bool = True, repos: list[RepoActivity] | None = None) -> GitHubSnapshot:
    return GitHubSnapshot(
        collected_at=datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc),
        complete=complete,
        snapshot_digest="b" * 64,
        viewer_login="alice",
        repos=list(repos or []),
    )


def _profile_job(*, kind: str, fields: ProfileFields) -> JobRequest:
    return JobRequest(
        job_id="job-profile",
        user_id="user-1",
        job_type="profile_document",
        document=DocumentSpec(kind=kind, template_version="v1", profile_fields=fields),
    )


def test_resume_blocked_without_education() -> None:
    job = _profile_job(
        kind="resume",
        fields=ProfileFields(name="홍길동", experience_md="백엔드 2년", education_md=""),
    )
    proposal = build_artifact(_snapshot(), job)

    assert proposal.status == "blocked"
    assert proposal.kind == "resume"
    assert proposal.error is not None
    assert proposal.error.code == "blocked"
    assert proposal.proposed_action is None
    assert "education_md" in proposal.unresolved_fields
    UUID(proposal.proposal_id)
    assert proposal.proposal_digest


def test_skills_empty_when_no_languages_does_not_invent_python() -> None:
    repos = [
        RepoActivity(
            owner="acme",
            name="demo",
            description="샘플 저장소",
            topics=[],
            languages=[],
            manifest_files=[],
        )
    ]
    job = _profile_job(kind="portfolio", fields=ProfileFields(name="홍길동"))
    proposal = build_artifact(_snapshot(repos=repos), job)

    assert proposal.status == "proposed"
    assert proposal.kind == "portfolio"
    assert proposal.template_ref is not None
    assert proposal.template_ref.kind == "portfolio"
    assert proposal.template_ref.version == "v1"
    expected_sha = sha256(template_path("portfolio", "v1").read_bytes()).hexdigest()
    assert proposal.template_ref.sha256 == expected_sha
    assert "Python" not in proposal.body_markdown
    assert "홍길동" in proposal.body_markdown
    assert all(ref.field != "skills_md" for ref in proposal.evidence_refs)
    assert "skills_md" in proposal.unresolved_fields


def test_skills_md_from_languages_topics_manifest_only() -> None:
    repos = [
        RepoActivity(
            owner="acme",
            name="demo",
            languages=[LanguageShare(name="Go", bytes=1200)],
            topics=["cli"],
            manifest_files=["go.mod"],
        )
    ]
    job = _profile_job(kind="portfolio", fields=ProfileFields(name="홍길동"))
    proposal = build_artifact(_snapshot(repos=repos), job)

    assert proposal.status == "proposed"
    assert "Go" in proposal.body_markdown
    assert "cli" in proposal.body_markdown
    assert "go.mod" in proposal.body_markdown
    assert "Python" not in proposal.body_markdown
    assert any(
        ref.field == "skills_md" and ref.source_type == "language" and ref.source_id == "Go"
        for ref in proposal.evidence_refs
    )
    assert "skills_md" not in proposal.unresolved_fields


def test_readme_no_change_when_identical() -> None:
    content = "# demo\n\n기존 소개입니다.\n"
    repos = [
        RepoActivity(
            owner="acme",
            name="demo",
            default_branch="main",
            head_sha="headsha1",
            description="샘플",
            readme=ReadmeBlob(path="README.md", blob_sha="blobsha1", content=content),
        )
    ]
    job = JobRequest(
        job_id="job-readme",
        user_id="user-1",
        job_type="readme_proposal",
        readme=ReadmeTarget(owner="acme", repo="demo", path="README.md"),
    )
    proposal = build_artifact(_snapshot(repos=repos), job)

    assert proposal.status == "no_change"
    assert proposal.kind == "readme"
    assert proposal.proposed_action is None
    assert proposal.action_digest is None
    assert proposal.proposal_digest


def test_readme_proposed_when_heading_needs_fix() -> None:
    content = "#demo\n"
    repos = [
        RepoActivity(
            owner="acme",
            name="demo",
            default_branch="main",
            head_sha="headsha1",
            readme=ReadmeBlob(path="README.md", blob_sha="blobsha1", content=content),
        )
    ]
    job = JobRequest(
        job_id="job-readme",
        user_id="user-1",
        job_type="readme_proposal",
        readme=ReadmeTarget(owner="acme", repo="demo", path="README.md"),
    )
    proposal = build_artifact(_snapshot(repos=repos), job)

    assert proposal.status == "proposed"
    assert proposal.kind == "readme"
    assert proposal.proposed_action is not None
    assert proposal.proposed_action.type == "create_readme_pr"
    assert proposal.proposed_action.replacement_markdown == "# demo\n"
    assert proposal.proposed_action.expected_base_sha == "headsha1"
    assert proposal.proposed_action.expected_blob_sha == "blobsha1"
    assert proposal.proposed_action.owner == "acme"
    assert proposal.proposed_action.repo == "demo"
    assert proposal.proposed_action.path == "README.md"
    assert proposal.action_digest
    assert proposal.body_markdown == "# demo\n"


def test_readme_appends_recent_activity() -> None:
    content = "# demo\n\n소개입니다.\n"
    repos = [
        RepoActivity(
            owner="acme",
            name="demo",
            default_branch="main",
            head_sha="headsha1",
            commits=[CommitSummary(sha="abc1234", message="로그인 수정")],
            readme=ReadmeBlob(path="README.md", blob_sha="blobsha1", content=content),
        )
    ]
    job = JobRequest(
        job_id="job-readme",
        user_id="user-1",
        job_type="readme_proposal",
        readme=ReadmeTarget(owner="acme", repo="demo", path="README.md"),
    )
    proposal = build_artifact(_snapshot(repos=repos), job)

    assert proposal.status == "proposed"
    assert proposal.proposed_action is not None
    assert "최근 활동" in proposal.body_markdown
    assert "로그인 수정" in proposal.body_markdown
    assert proposal.action_digest
    assert proposal.proposed_action.expected_blob_sha == "blobsha1"
