"""Resume section rendering. Compact: a recruiter scans, so one line per fact."""

from __future__ import annotations

from app.analyze import skills as skill_analysis
from app.contracts import Evidence, EvidenceRef, ProjectFacts
from app.pipelines import common

Section = tuple[str, list[EvidenceRef]]

MAX_HIGHLIGHTS = 3


def summary(evidence: Evidence, extra_lines: list[str] | None = None) -> Section:
    if evidence.is_empty():
        return "", []
    refs: list[EvidenceRef] = [
        common.project_ref("summary_md", p) for p in evidence.projects
    ]
    if extra_lines:
        return "\n\n".join(extra_lines), refs

    span = common.period(evidence.period_start, evidence.period_end)
    top = [s.name for s in evidence.skills if s.category in ("language", "framework")][:4]
    clauses: list[str] = []
    if span:
        clauses.append(f"{span} 동안 프로젝트 {len(evidence.projects)}개를 진행했습니다")
    if top:
        clauses.append(f"주요 기술은 {', '.join(top)} 입니다")
        refs.extend(
            common.skill_ref("summary_md", s)
            for s in evidence.skills
            if s.name in top
        )
    if not clauses:
        return "", []
    return ". ".join(clauses) + ".", refs


def skills(evidence: Evidence) -> Section:
    grouped = skill_analysis.group_by_category(evidence.skills)
    if not grouped:
        return "", []
    lines: list[str] = []
    refs: list[EvidenceRef] = []
    for label, members in grouped:
        lines.append(f"- **{label}**: {', '.join(s.name for s in members)}")
        refs.extend(common.skill_ref("skills_md", s) for s in members)
    return "\n".join(lines), refs


def projects(evidence: Evidence) -> Section:
    if not evidence.projects:
        return "", []
    blocks: list[str] = []
    refs: list[EvidenceRef] = []
    for facts in evidence.projects:
        block, block_refs = _project_block(facts)
        blocks.append(block)
        refs.extend(block_refs)
    return "\n\n".join(blocks), refs


def _project_block(facts: ProjectFacts) -> Section:
    refs = [common.project_ref("projects_md", facts)]
    span = common.period(facts.started_at, facts.ended_at)
    heading = f"### {facts.repo}"
    if span:
        heading += f" ({span})"
    parts = [heading, ""]

    meta = [common.team_label(facts)]
    stack = ", ".join(s.name for s in facts.languages)
    if stack:
        meta.append(stack)
        refs.extend(common.skill_ref("projects_md", s) for s in facts.languages)
    scale = common.scale(facts)
    if scale:
        meta.append(scale)
    parts.append(f"- {' · '.join(meta)}")

    if facts.description:
        parts.append(f"- {facts.description}")
    for highlight in facts.highlights[:MAX_HIGHLIGHTS]:
        parts.append(f"- {highlight.subject}")
        refs.append(common.commit_ref("projects_md", highlight))
    return "\n".join(parts).rstrip(), refs
