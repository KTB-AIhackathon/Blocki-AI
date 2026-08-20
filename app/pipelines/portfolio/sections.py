"""Portfolio section rendering. Depth over brevity: full project detail."""

from __future__ import annotations

from app.analyze import skills as skill_analysis
from app.contracts import Evidence, EvidenceRef, ProjectFacts
from app.pipelines import common

Section = tuple[str, list[EvidenceRef]]

TOP_SKILLS_IN_SUMMARY = 4


def summary(evidence: Evidence, extra_lines: list[str] | None = None) -> Section:
    if evidence.is_empty():
        return "", []
    refs: list[EvidenceRef] = []
    lines: list[str] = list(extra_lines or [])

    if not lines:
        top = [s for s in evidence.skills if s.category in ("language", "framework")][
            :TOP_SKILLS_IN_SUMMARY
        ]
        if top:
            names = ", ".join(s.name for s in top)
            lines.append(f"주로 사용하는 기술은 {names} 입니다.")
            refs.extend(common.skill_ref("summary_md", s) for s in top)
        span = common.period(evidence.period_start, evidence.period_end)
        if span and evidence.projects:
            lines.append(
                f"{span} 동안 프로젝트 {len(evidence.projects)}개에 "
                f"커밋 {evidence.my_commits}개를 남겼습니다."
            )
            refs.extend(common.project_ref("summary_md", p) for p in evidence.projects)
    else:
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
    blocks: list[str] = []
    refs: list[EvidenceRef] = []
    for label, members in grouped:
        bullets = [f"**{label}**", ""]
        for skill in members:
            if skill.measured and skill.weight >= 0.01:
                bullets.append(f"- {skill.name} ({skill.weight * 100:.0f}%)")
            else:
                bullets.append(f"- {skill.name}")
            refs.append(common.skill_ref("skills_md", skill))
        blocks.append("\n".join(bullets))
    return "\n\n".join(blocks), refs


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
    parts = [f"### {facts.repo}", ""]
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

    if facts.highlights:
        parts.extend(["", "**주요 작업**", ""])
        for highlight in facts.highlights:
            parts.append(f"- {highlight.subject} (`{highlight.sha[:7]}`)")
            refs.append(common.commit_ref("projects_md", highlight))
    return "\n".join(parts).rstrip(), refs
