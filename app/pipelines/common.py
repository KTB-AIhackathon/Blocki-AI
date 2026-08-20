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
    TilFact,
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


def duration_months(start: datetime | None, end: datetime | None) -> int | None:
    if start is None or end is None:
        return None
    return max((end.year - start.year) * 12 + end.month - start.month + 1, 1)


def project_ref(field: str, project: ProjectFacts) -> EvidenceRef:
    return EvidenceRef(
        field=field, repo=project.repo, source_type="repo", source_id=project.id
    )


def commit_ref(field: str, commit: CommitFact) -> EvidenceRef:
    return EvidenceRef(
        field=field, repo=commit.repo, source_type="commit", source_id=commit.id
    )


def til_ref(field: str, til: TilFact) -> EvidenceRef:
    return EvidenceRef(field=field, repo="", source_type="til", source_id=til.id)


def til_field_ref(field: str, til: TilFact, source_field: str) -> EvidenceRef:
    return EvidenceRef(
        field=field,
        repo="",
        source_type="til",
        source_id=f"{til.id}:{source_field}",
    )


def til_field_refs(field: str, til: TilFact, *source_fields: str) -> list[EvidenceRef]:
    return [til_ref(field, til), *[til_field_ref(field, til, source) for source in source_fields]]


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


def contribution(project: ProjectFacts) -> str:
    if project.total_commits <= 0 or project.total_commits < project.my_commits:
        result = f"커밋 {project.my_commits}개"
    else:
        percentage = project.my_commits / project.total_commits * 100
        result = (
            f"커밋 {project.my_commits}개 "
            f"(전체 {project.total_commits}개 중 {percentage:.0f}%)"
        )
    if project.merged_prs:
        result += f", 머지된 PR {project.merged_prs}개"
    if project.closed_issues:
        result += f", 해결한 이슈 {project.closed_issues}개"
    return result


def team_label(project: ProjectFacts) -> str:
    if project.contributors <= 1:
        return "개인 프로젝트"
    return f"팀 프로젝트 ({project.contributors}명)"


def selection(
    evidence: Evidence, selected: list[ProjectFacts] | None = None
) -> tuple[str, list[EvidenceRef]]:
    candidates = evidence.selection_candidates or evidence.projects
    selected_repos = {project.repo for project in (selected or evidence.projects)}
    labels = {
        project.repo: _selection_label(project.repo, [item.repo for item in candidates])
        for project in candidates
    }
    lines: list[str] = []
    if evidence.selection_reason.strip():
        lines.extend([f"> {evidence.selection_reason.strip()}", ""])
    lines.extend(["| 저장소 | 점수 | 주요 근거 |", "|---|---:|---|"])
    refs: list[EvidenceRef] = []
    for project in sorted(candidates, key=lambda item: (-item.score, item.repo)):
        dropped = project.repo not in selected_repos
        repo = f"~~{labels[project.repo]}~~" if dropped else labels[project.repo]
        lines.append(f"| {repo} | {project.score:.1f} | {_selection_evidence(project, dropped)} |")
        refs.append(project_ref("selection_md", project))
    return "\n".join(lines), refs


def _selection_label(repo: str, repos: list[str]) -> str:
    short = repo.rsplit("/", 1)[-1]
    return repo if sum(item.rsplit("/", 1)[-1] == short for item in repos) > 1 else short


def _selection_evidence(project: ProjectFacts, dropped: bool) -> str:
    breakdown = project.score_breakdown
    facts: list[str] = []
    if project.my_commits:
        facts.append(f"커밋 {project.my_commits}")
    if project.til:
        facts.append(f"TIL {len(project.til)}건")
    if breakdown.get("team", 0) > 0:
        facts.append(f"팀 {project.contributors}명")
    if breakdown.get("award", 0) > 0:
        facts.append("수상")
    if breakdown.get("recency", 0) > 0:
        facts.append("최근 활동")
    penalized = breakdown.get("penalty", 0) < 0 or project.repo.rsplit("/", 1)[-1].casefold().endswith("-log")
    if penalized:
        facts.append("학습 저장소 감점으로 제외" if dropped else "학습 저장소 감점")
    return " · ".join(facts or ["추가 근거 없음"])
