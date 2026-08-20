"""Resume section rendering. Compact: a recruiter scans, so one line per fact."""

from __future__ import annotations

from app.analyze import skills as skill_analysis
from app.contracts import Evidence, EvidenceRef, ProjectFacts, SkillFact
from app.pipelines import common

Section = tuple[str, list[EvidenceRef]]

MAX_HIGHLIGHTS = 2


def summary(evidence: Evidence, extra_lines: list[str] | None = None) -> Section:
    lines = [line.strip() for line in (extra_lines or []) if line.strip()]
    if not lines:
        return "", []
    refs = [common.project_ref("summary_md", p) for p in evidence.projects]
    return "\n\n".join(lines), refs


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
    lines: list[str] = []
    refs: list[EvidenceRef] = []
    for facts in evidence.projects:
        line, line_refs = _project_line(facts, evidence.skills)
        lines.append(line)
        refs.extend(line_refs)
    return "\n".join(lines), refs


def _project_line(facts: ProjectFacts, catalogue: list[SkillFact]) -> Section:
    refs = [common.project_ref("projects_md", facts)]
    title = (facts.description or "").strip() or facts.repo.rsplit("/", 1)[-1]
    shown = [item for item in facts.highlights if (item.subject or "").strip()][
        :MAX_HIGHLIGHTS
    ]
    work = ", ".join(item.subject.strip() for item in shown)
    stack = [skill for skill in catalogue if facts.repo in skill.repos]
    if stack:
        refs.extend(
            common.skill_ref(
                "projects_md", skill.model_copy(update={"repos": [facts.repo]})
            )
            for skill in stack
        )
    for highlight in shown:
        refs.append(common.commit_ref("projects_md", highlight))

    names = ", ".join(skill.name for skill in stack)
    if work and names:
        return f"- {title} — {work} `{names}`", refs
    if work:
        return f"- {title} — {work}", refs
    if names:
        return f"- {title} `{names}`", refs
    return f"- {title}", refs
