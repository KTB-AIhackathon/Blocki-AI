"""Portfolio section rendering. Depth over brevity: full project detail."""

from __future__ import annotations

from app.analyze import skills as skill_analysis
from app.contracts import Evidence, EvidenceRef, ProjectFacts
from app.llm.guard import GroundedText
from app.pipelines import common

Section = tuple[str, list[EvidenceRef]]

TOP_SKILLS_IN_SUMMARY = 4
MAX_HIGHLIGHTS_SHOWN = 3


def summary(evidence: Evidence, extra: list[GroundedText] | None = None) -> Section:
    if evidence.is_empty():
        return "", []
    extras = [item for item in (extra or []) if item.text.strip()]
    if extras:
        lines = [item.text.strip() for item in extras]
        return "\n\n".join(lines), _refs_from_grounded("summary_md", extras, evidence)

    refs: list[EvidenceRef] = []
    lines: list[str] = []
    top = [s for s in evidence.skills if s.category in ("language", "framework")][
        :TOP_SKILLS_IN_SUMMARY
    ]
    if top:
        names = ", ".join(s.name for s in top)
        lines.append(f"주로 사용하는 기술은 {names} 입니다.")
        refs.extend(common.skill_ref("summary_md", s) for s in top)
    elif (span := common.period(evidence.period_start, evidence.period_end)) and evidence.projects:
        lines.append(
            f"{span} 동안 프로젝트 {len(evidence.projects)}개에 "
            f"커밋 {evidence.my_commits}개를 남겼습니다."
        )
        refs.extend(common.project_ref("summary_md", p) for p in evidence.projects)

    return "\n\n".join(lines), refs


def stats(evidence: Evidence) -> Section:
    rows: list[tuple[str, str]] = []
    span = common.period(evidence.period_start, evidence.period_end)
    if span:
        rows.append(("활동 기간", span))
    if evidence.projects:
        rows.append(("프로젝트", f"{len(evidence.projects)}개"))
    if evidence.my_commits:
        rows.append(("커밋", f"{evidence.my_commits}개"))
    merged = sum(p.merged_prs for p in evidence.projects)
    if merged:
        rows.append(("머지된 PR", f"{merged}개"))
    closed = sum(p.closed_issues for p in evidence.projects)
    if closed:
        rows.append(("해결한 이슈", f"{closed}개"))
    if not rows:
        return "", []
    table = ["| 항목 | 값 |", "| --- | --- |"]
    table.extend(f"| {label} | {value} |" for label, value in rows)
    refs = [common.project_ref("stats_md", p) for p in evidence.projects]
    return "\n".join(table), refs


def skills(evidence: Evidence) -> Section:
    grouped = skill_analysis.group_by_category(evidence.skills)
    if not grouped:
        return "", []
    featured = [project.repo for project in evidence.projects]
    blocks: list[str] = []
    refs: list[EvidenceRef] = []
    for label, members in grouped:
        lines: list[str] = []
        for skill in members:
            used = [repo for repo in featured if repo in skill.repos]
            if not used:
                continue
            names = ", ".join(_repo_label(repo, featured) for repo in used)
            lines.append(f"- {skill.name} — {names}")
            refs.extend(
                common.skill_ref("skills_md", skill.model_copy(update={"repos": [repo]}))
                for repo in used
            )
        if lines:
            blocks.append("\n".join([f"**{label}**", "", *lines]))
    return "\n\n".join(blocks), refs


def projects(
    evidence: Evidence, summaries: dict[str, GroundedText] | None = None
) -> Section:
    if not evidence.projects:
        return "", []
    featured = [project.repo for project in evidence.projects]
    blocks: list[str] = []
    refs: list[EvidenceRef] = []
    for facts in evidence.projects:
        block, block_refs = _project_block(
            facts, (summaries or {}).get(facts.id), featured
        )
        blocks.append(block)
        refs.extend(block_refs)
    return "\n\n".join(blocks), refs


def _project_block(
    facts: ProjectFacts,
    summary: GroundedText | None,
    featured: list[str],
) -> Section:
    refs = [common.project_ref("projects_md", facts)]
    parts = [f"### {_repo_label(facts.repo, featured)}", ""]
    if summary is not None:
        parts.extend([summary.text.strip(), ""])
    if facts.description:
        parts.extend([f"> {facts.description}", ""])

    meta: list[str] = []
    span = common.period(facts.started_at, facts.ended_at)
    if span:
        meta.append(f"- 기간: {span}")
    meta.append(f"- 구성: {common.team_label(facts)}")
    scale = common.scale(facts)
    if scale:
        meta.append(f"- 규모: {scale}")
    stack = ", ".join(s.name for s in facts.languages)
    if stack:
        meta.append(f"- 기술: {stack}")
        refs.extend(common.skill_ref("projects_md", s) for s in facts.languages)
    if facts.url:
        meta.append(f"- 저장소: {facts.url}")
    parts.extend(meta)

    shown = [item for item in facts.highlights if (item.subject or "").strip()][
        :MAX_HIGHLIGHTS_SHOWN
    ]
    if shown:
        parts.extend(["", "**주요 작업**", ""])
        for highlight in shown:
            parts.append(f"- {highlight.subject.strip()}")
            refs.append(common.commit_ref("projects_md", highlight))
    return "\n".join(parts).rstrip(), refs


def _short_name(repo: str) -> str:
    return repo.rsplit("/", 1)[-1]


def _repo_label(repo: str, featured: list[str]) -> str:
    short = _short_name(repo)
    if sum(1 for item in featured if _short_name(item) == short) > 1:
        return repo
    return short


def _refs_from_grounded(
    field: str, items: list[GroundedText], evidence: Evidence
) -> list[EvidenceRef]:
    by_id: dict[str, EvidenceRef] = {}
    for project in evidence.projects:
        by_id[project.id] = common.project_ref(field, project)
        for highlight in project.highlights:
            by_id[highlight.id] = common.commit_ref(field, highlight)
        for skill in project.languages:
            by_id[skill.id] = common.skill_ref(field, skill)
    for skill in evidence.skills:
        by_id.setdefault(skill.id, common.skill_ref(field, skill))
    refs: list[EvidenceRef] = []
    seen: set[str] = set()
    for item in items:
        for source_id in item.evidence_ids:
            ref = by_id.get(source_id)
            if ref is None or source_id in seen:
                continue
            seen.add(source_id)
            refs.append(ref)
    return refs
