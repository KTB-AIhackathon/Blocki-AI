"""Portfolio section rendering. Selected repos, not a dashboard."""

from __future__ import annotations

import re

from app.analyze import skills as skill_analysis
from app.contracts import Evidence, EvidenceRef, ProjectFacts, SkillFact
from app.llm.guard import GroundedText
from app.pipelines import common
from app.pipelines.portfolio.team import Dossier

Section = tuple[str, list[EvidenceRef]]

MAX_GOALS = 3
MAX_ACHIEVEMENTS = 4
MAX_GROWTH = 4
_AWARD = re.compile(r"수상|우수상|대상|최우수|입상|1위|2위|award|winner", re.IGNORECASE)


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
        names = list(dict.fromkeys(skill.name for skill in members if _used_in(skill, featured)))
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
    for index, facts in enumerate(evidence.projects, 1):
        block, block_refs = _project_block(
            facts, index, featured, evidence.skills, by_id.get(facts.id)
        )
        blocks.append(block)
        refs.extend(block_refs)
    return "\n\n".join(blocks), refs


def learning(_evidence: Evidence) -> Section:
    return "", []


def _project_block(
    facts: ProjectFacts,
    number: int,
    featured: list[str],
    catalogue: list[SkillFact],
    dossier: Dossier | None = None,
) -> Section:
    refs = [common.project_ref("projects_md", facts)]
    refs.extend(common.commit_ref("projects_md", item) for item in facts.highlights)
    refs.extend(common.work_ref("projects_md", item) for item in [*facts.pull_requests, *facts.issues])
    refs.extend(common.til_ref("projects_md", item) for item in facts.til)
    name = _repo_label(facts.repo, featured)
    description = (facts.description or "").strip()
    heading = f"### {number}. {name}"
    if description:
        heading += f" — {description}"
    parts = [heading, "", "**개요**"]
    award = (facts.award or "").strip() or _award_from_description(description)
    if award:
        parts.append(f"- 수상: {award}")
    span = common.period(facts.started_at, facts.ended_at)
    months = common.duration_months(facts.started_at, facts.ended_at)
    if span and months is not None:
        span += f" ({months}개월)"
    # 개요는 GitHub이 대는 사실이라 비면 줄을 뺀다. 사용자가 채울 칸은 목표·성과·성장이다.
    if span:
        parts.append(f"- 기간: {span}")
    parts.append(f"- 인원: {common.team_label(facts)}")
    parts.append(f"- 역할: {common.contribution(facts)}")
    stack = list({skill.name: skill for skill in catalogue if facts.repo in skill.repos}.values())
    if not stack:
        stack = list(facts.languages)
    if stack:
        parts.append(f"- 기술 스택: {', '.join(skill.name for skill in stack)}")
    if facts.url:
        parts.append(f"- 링크: {facts.url}")
    lead = (facts.readme_lead or "").strip()
    if lead and lead != description:
        parts.append(f"- {lead}")
    refs.extend(common.skill_ref("projects_md", skill.model_copy(update={"repos": [facts.repo]})) for skill in stack)
    if dossier is not None:
        for item in dossier.pitch:
            text = item.text.strip()
            if text and text != description:
                parts.append(f"- {text}")
                refs.extend(
                    _refs_from_grounded("projects_md", [item], _evidence_for(facts, catalogue))
                )
    parts.extend(["", "**목표**"])
    goals = _til_values(facts, "goal", MAX_GOALS)
    if goals:
        parts.extend(f"- {value}" for value, _item, _field in goals)
        for _value, item, _field in goals:
            refs.extend(common.til_field_refs("projects_md", item, "goal"))
    else:
        parts.append(f"- {_fill_text()}")

    parts.extend(["", "**성과**"])
    achievements = _achievements(facts, dossier)
    if achievements:
        for achievement_number, (item, text, metric) in enumerate(achievements, 1):
            parts.extend(["", f"#### {achievement_number}. {item.title}", f"- {text}"])
            refs.extend(common.til_field_refs("projects_md", item, "result" if item.result else "attempt"))
            if metric:
                parts.append(f"- {metric}")
                refs.extend(common.til_field_refs("projects_md", item, "metric"))
    else:
        # 커밋 제목은 성과가 아니다. TIL 이 없으면 사용자가 직접 채운다.
        parts.append(f"- {_fill_text()}")

    parts.extend(["", "**성장**"])
    growth = _til_values(facts, "learned", MAX_GROWTH) + _til_values(facts, "retro", MAX_GROWTH)
    if growth:
        for value, item, field in growth[:MAX_GROWTH]:
            parts.append(f"- {value}")
            refs.extend(common.til_field_refs("projects_md", item, field))
    elif facts.til:
        added = False
        for item in facts.til:
            title = (item.title or "").strip()
            if not title:
                continue
            parts.append(f"- {item.date:%Y-%m-%d} · {title}")
            refs.append(common.til_ref("projects_md", item))
            added = True
        if not added:
            parts.append(f"- {_fill_text()}")
    else:
        parts.append(f"- {_fill_text()}")
    return "\n".join(parts).rstrip(), refs


def _til_values(
    facts: ProjectFacts, field: str, limit: int
) -> list[tuple[str, object, str]]:
    """TIL 한 건에서 한 줄만. 한 기록의 여러 줄이 상한을 다 먹으면 나머지 기록이
    통째로 사라진다."""
    values: list[tuple[str, object, str]] = []
    seen: set[str] = set()
    for item in facts.til:
        lines = [line.strip() for line in getattr(item, field).strip().splitlines()]
        value = next((line for line in lines if line), "")
        if not value or value.casefold() in seen:
            continue
        seen.add(value.casefold())
        values.append((value, item, field))
        if len(values) >= limit:
            break
    return values


def _achievements(facts: ProjectFacts, dossier: Dossier | None) -> list[tuple[object, str, str]]:
    by_id = {item.id: item for item in facts.til}
    ordered = [by_id[item_id] for item_id in (dossier.work_ids if dossier else []) if item_id in by_id]
    ordered.extend(item for item in facts.til if item not in ordered)
    out: list[tuple[object, str, str]] = []
    # 시도·결과가 적힌 TIL 이 하나라도 있으면 그것만 성과로 쓴다. 학습만 적힌 기록이
    # 성과 자리를 밀어내면 안 된다. 그런 기록밖에 없을 때는 제목만 남긴다. 사용자가
    # 그 프로젝트에 대해 쓴 것은 맞으니 버릴 이유는 없다.
    has_work = any(item.attempt.strip() or item.result.strip() for item in ordered)
    for item in ordered:
        text = item.result.strip() or item.attempt.strip()
        if not text and not has_work:
            text = item.title.strip()
        if not text:
            continue
        out.append((item, text.splitlines()[0], item.metric.text() if item.metric else ""))
        if len(out) >= MAX_ACHIEVEMENTS:
            break
    return out




def _award_from_description(description: str) -> str:
    return description if _AWARD.search(description) else ""


def _fill_text() -> str:
    return common.FILL_IN.removeprefix("> ")


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
