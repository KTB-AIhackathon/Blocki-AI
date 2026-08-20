"""Portfolio section rendering. Selected repos, not a dashboard."""

from __future__ import annotations

from app.analyze import skills as skill_analysis
from app.contracts import Evidence, EvidenceRef, ProjectFacts, SkillFact
from app.llm.guard import GroundedText
from app.pipelines import common
from app.pipelines.portfolio.team import Dossier

Section = tuple[str, list[EvidenceRef]]

MAX_HIGHLIGHTS_SHOWN = 3


def summary(evidence: Evidence, extra: list[GroundedText] | None = None) -> Section:
    extras = [item for item in (extra or []) if item.text.strip()]
    if not extras:
        return "", []
    return "\n\n".join(item.text.strip() for item in extras), _refs_from_grounded(
        "summary_md", extras, evidence
    )


def skills(evidence: Evidence) -> Section:
    grouped = skill_analysis.group_by_category(evidence.skills)
    if not grouped:
        return "", []
    featured = [project.repo for project in evidence.projects]
    blocks: list[str] = []
    refs: list[EvidenceRef] = []
    for label, members in grouped:
        names = [skill.name for skill in members if _used_in(skill, featured)]
        if not names:
            continue
        blocks.append(f"- **{label}**: {', '.join(names)}")
        refs.extend(
            common.skill_ref("skills_md", skill)
            for skill in members
            if skill.name in names
        )
    return "\n".join(blocks), refs


def projects(evidence: Evidence, dossiers: list[Dossier] | None = None) -> Section:
    if not evidence.projects:
        return "", []
    featured = [project.repo for project in evidence.projects]
    by_id = {dossier.project_id: dossier for dossier in dossiers or []}
    blocks: list[str] = []
    refs: list[EvidenceRef] = []
    for facts in evidence.projects:
        block, block_refs = _project_block(
            facts, featured, evidence.skills, by_id.get(facts.id)
        )
        blocks.append(block)
        refs.extend(block_refs)
    return "\n\n".join(blocks), refs


def learning(_evidence: Evidence) -> Section:
    return "", []


def _project_block(
    facts: ProjectFacts,
    featured: list[str],
    catalogue: list[SkillFact],
    dossier: Dossier | None = None,
) -> Section:
    refs = [common.project_ref("projects_md", facts)]
    parts = [f"### {_repo_label(facts.repo, featured)}", ""]
    if facts.description:
        parts.extend([f"> {facts.description}", ""])
    if dossier is not None:
        for item in dossier.pitch:
            text = item.text.strip()
            if text and text != (facts.description or "").strip():
                parts.append(text)
                refs.extend(
                    _refs_from_grounded("projects_md", [item], _evidence_for(facts, catalogue))
                )
                parts.append("")

    meta: list[str] = []
    span = common.period(facts.started_at, facts.ended_at)
    if span:
        months = common.duration_months(facts.started_at, facts.ended_at)
        suffix = f" ({months}개월)" if months is not None else ""
        meta.append(f"- 기간: {span}{suffix}")
    meta.append(f"- 구성: {common.team_label(facts)}")
    meta.append(f"- 기여: {common.contribution(facts)}")
    parts.extend(meta)

    stack = [skill for skill in catalogue if facts.repo in skill.repos]
    if stack:
        parts.append(f"- 기술: {', '.join(skill.name for skill in stack)}")
        refs.extend(
            common.skill_ref(
                "projects_md", skill.model_copy(update={"repos": [facts.repo]})
            )
            for skill in stack
        )
    if facts.url:
        parts.append(f"- 저장소: {facts.url}")

    titles = _work_titles(facts, dossier.work_ids if dossier is not None else [])
    if titles:
        parts.extend(["", "**주요 작업**", ""])
        for title, ref in titles[:MAX_HIGHLIGHTS_SHOWN]:
            parts.append(f"- {title}")
            refs.append(ref)
    else:
        shown = [item for item in facts.highlights if (item.subject or "").strip()][
            :MAX_HIGHLIGHTS_SHOWN
        ]
        if shown:
            parts.extend(["", "**주요 작업**", ""])
            for highlight in shown:
                parts.append(f"- {highlight.subject.strip()}")
                refs.append(common.commit_ref("projects_md", highlight))
    if facts.til:
        parts.extend(["", "**배운 것**", ""])
        for item in facts.til:
            parts.append(f"- {item.date:%Y-%m-%d} · {item.title}")
            refs.append(common.til_ref("projects_md", item))
    return "\n".join(parts).rstrip(), refs


def _work_titles(
    facts: ProjectFacts, work_ids: list[str]
) -> list[tuple[str, EvidenceRef]]:
    by_id: dict[str, tuple[str, EvidenceRef]] = {}
    for highlight in facts.highlights:
        title = (highlight.subject or "").strip()
        if title:
            by_id[highlight.id] = (title, common.commit_ref("projects_md", highlight))
    for item in [*facts.pull_requests, *facts.issues]:
        title = (item.title or "").strip()
        if title:
            by_id[item.id] = (title, common.work_ref("projects_md", item))
    for item in facts.til:
        title = (item.title or "").strip()
        if title:
            by_id[item.id] = (title, common.til_ref("projects_md", item))
    out: list[tuple[str, EvidenceRef]] = []
    for source_id in work_ids:
        found = by_id.get(source_id)
        if found is not None:
            out.append(found)
        if len(out) >= MAX_HIGHLIGHTS_SHOWN:
            break
    return out


def _evidence_for(facts: ProjectFacts, catalogue: list[SkillFact]) -> Evidence:
    return Evidence(projects=[facts], skills=catalogue)


def _used_in(skill: SkillFact, featured: list[str]) -> bool:
    return any(repo in skill.repos for repo in featured)


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
        for item in project.pull_requests:
            by_id[item.id] = common.work_ref(field, item)
        for item in project.issues:
            by_id[item.id] = common.work_ref(field, item)
        for item in project.til:
            by_id[item.id] = common.til_ref(field, item)
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
