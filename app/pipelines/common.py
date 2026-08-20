"""Helpers shared by the two document pipelines. No rendering decisions here."""

from __future__ import annotations

from datetime import datetime

from app.contracts import (
    ArtifactProposal,
    CommitFact,
    DocumentKind,
    EvidenceRef,
    JobError,
    JobRequest,
    ProfileFields,
    ProjectFacts,
    SkillFact,
    TemplateRef,
    WorkItem,
)


# Career and schooling cannot be derived from GitHub, and inventing them is the
# one thing this worker must never do. Rather than refusing to produce a resume,
# leave the section as a visible blank: the document is published to Notion, and
# Notion is where the user fills it in. The field is still reported through
# `unresolved_fields` so the UI can say what is missing.
FILL_IN = "> 아직 비어 있습니다. 이 Notion 페이지에서 직접 채워주세요."

USER_SUPPLIED: dict[DocumentKind, tuple[str, ...]] = {
    "portfolio": (),
    "resume": ("experience_md", "education_md"),
}


def required_missing(kind: DocumentKind, fields: ProfileFields) -> list[str]:
    """Fields with no sensible blank. Only the name, which titles the document."""
    return [] if fields.name.strip() else ["name"]


def user_supplied(kind: DocumentKind, fields: ProfileFields) -> tuple[dict[str, str], list[str]]:
    """Return the section bodies to render and the ones left for the user."""
    values: dict[str, str] = {}
    blank: list[str] = []
    for field in USER_SUPPLIED.get(kind, ()):
        text = getattr(fields, field, "").strip()
        values[field] = text or FILL_IN
        if not text:
            blank.append(field)
    return values, blank


def blocked(
    job: JobRequest,
    kind: DocumentKind,
    missing: list[str],
    template_ref: TemplateRef | None = None,
) -> ArtifactProposal:
    return ArtifactProposal(
        proposal_id="",
        job_id=job.job_id,
        status="blocked",
        kind=kind,
        template_ref=template_ref,
        unresolved_fields=missing,
        error=JobError(
            code="blocked",
            message="필수 프로필 필드 누락: " + ", ".join(missing),
            retryable=False,
        ),
    )


def period(start: datetime | None, end: datetime | None) -> str:
    if start is None and end is None:
        return ""
    if start is None or end is None:
        only = start or end
        assert only is not None
        return only.strftime("%Y.%m")
    if start.strftime("%Y.%m") == end.strftime("%Y.%m"):
        return start.strftime("%Y.%m")
    return f"{start.strftime('%Y.%m')} ~ {end.strftime('%Y.%m')}"


def project_ref(field: str, project: ProjectFacts) -> EvidenceRef:
    return EvidenceRef(
        field=field, repo=project.repo, source_type="repo", source_id=project.id
    )


def commit_ref(field: str, commit: CommitFact) -> EvidenceRef:
    return EvidenceRef(
        field=field, repo=commit.repo, source_type="commit", source_id=commit.id
    )


def work_ref(field: str, item: WorkItem) -> EvidenceRef:
    return EvidenceRef(
        field=field, repo=item.repo, source_type=item.source_type, source_id=item.id
    )


def skill_ref(field: str, skill: SkillFact) -> EvidenceRef:
    return EvidenceRef(
        field=field,
        repo=skill.repos[0] if skill.repos else "",
        source_type="skill",
        source_id=skill.id,
    )


def scale(project: ProjectFacts) -> str:
    parts: list[str] = []
    if project.my_commits:
        parts.append(f"커밋 {project.my_commits}개")
    if project.merged_prs:
        parts.append(f"머지된 PR {project.merged_prs}개")
    if project.closed_issues:
        parts.append(f"해결한 이슈 {project.closed_issues}개")
    return ", ".join(parts)


def team_label(project: ProjectFacts) -> str:
    if project.contributors <= 1:
        return "개인 프로젝트"
    return f"팀 프로젝트 ({project.contributors}명)"
