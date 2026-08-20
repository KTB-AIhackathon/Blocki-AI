"""Resume section rendering from repository and TIL evidence."""

from __future__ import annotations

from app.analyze import skills as skill_analysis
from app.contracts import Evidence, EvidenceRef, ProjectFacts, SkillFact
from app.pipelines import common

Section = tuple[str, list[EvidenceRef]]
MAX_CARDS = 4
MAX_CONTRIBUTIONS = 4
# 한 항목에 들어갈 줄 수 상한. TIL 한 건의 `문제` 는 「문제 또는 목표」·「문제」·「원인」
# 세 키를 합친 것이라, 기록이 세 건이면 상한 없이 아홉 줄이 한 줄에 눌려 들어간다.
MAX_PROBLEMS = 3
MAX_GOALS = 3
MAX_RESULTS = 4


def summary(evidence: Evidence, lines: list[str] | None = None) -> Section:
    text = [line.strip() for line in (lines or []) if line.strip()]
    if not text:
        return "", []
    refs = [common.project_ref("summary_md", project) for project in evidence.projects]
    return "\n\n".join(text), refs


def skills(evidence: Evidence, readme_skills: str) -> Section:
    if readme_skills.strip():
        return readme_skills.strip(), []
    grouped = skill_analysis.group_by_category(evidence.skills)
    if not grouped:
        return common.FILL_IN, []
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
    if not blocks:
        return common.FILL_IN, []
    return "\n".join(blocks), refs


def projects(evidence: Evidence) -> Section:
    if not evidence.projects:
        return "", []
    featured = [project.repo for project in evidence.projects]
    blocks: list[str] = []
    refs: list[EvidenceRef] = []
    for number, facts in enumerate(evidence.projects[:MAX_CARDS], 1):
        block, block_refs = _project_block(facts, number, featured, evidence.skills)
        blocks.append(block)
        refs.extend(block_refs)
    return "\n\n".join(blocks), refs


def learning(evidence: Evidence) -> Section:
    if not evidence.unmatched_til:
        return "", []
    lines = ["## 📝 그 외 학습 기록", ""]
    refs: list[EvidenceRef] = []
    for item in evidence.unmatched_til:
        lines.append(f"- {item.date:%Y-%m-%d} · {item.title}")
        refs.append(common.til_ref("learning_md", item))
    return "\n".join(lines), refs


def _project_block(
    facts: ProjectFacts, number: int, featured: list[str], catalogue: list[SkillFact]
) -> Section:
    refs = [common.project_ref("projects_md", facts)]
    name = _repo_label(facts.repo, featured)
    description = (facts.description or "").strip()
    heading = f"### {number}. {name}"
    if description:
        heading += f" — {description}"
    lines = [heading, ""]
    lines.extend(_labelled("문제", _til_lines(facts, "problem", MAX_PROBLEMS)))
    lines.extend(_labelled("목표", _til_lines(facts, "goal", MAX_GOALS)))
    lines.append(f"- **기여**: {common.contribution(facts)}")
    bullets = _contribution_bullets(facts)
    for bullet, bullet_refs in bullets:
        lines.append(f"  - {bullet}")
        refs.extend(bullet_refs)
    if not bullets:
        lines.append(f"  - {_fill_text()}")
    lines.extend(_labelled("성과", _result_lines(facts)))

    stack = [skill for skill in catalogue if facts.repo in skill.repos]
    refs.extend(
        common.skill_ref("projects_md", skill.model_copy(update={"repos": [facts.repo]}))
        for skill in stack
    )
    for item in facts.til:
        refs.append(common.til_ref("projects_md", item))
        for field in ("problem", "goal", "attempt", "result", "metric", "learned", "retro"):
            if getattr(item, field) not in ("", None):
                refs.append(common.til_field_ref("projects_md", item, field))
    return "\n".join(lines), refs


def _labelled(label: str, values: list[str]) -> list[str]:
    """첫 값은 라벨 옆에, 나머지는 하위 불릿으로. 한 줄에 몰아 넣으면 Notion 이
    통짜 문단으로 렌더링해 읽을 수 없게 된다."""
    if not values:
        return [f"- **{label}**: {_fill_text()}"]
    return [f"- **{label}**: {values[0]}", *(f"  - {value}" for value in values[1:])]


def _til_lines(facts: ProjectFacts, field: str, limit: int) -> list[str]:
    """TIL 한 건에서 한 줄만 뽑는다. 첫 줄이 그 기록의 대표 문장이다."""
    return _capped(
        (getattr(item, field).strip().splitlines() or [""])[0] for item in facts.til
    )[:limit]


def _result_lines(facts: ProjectFacts) -> list[str]:
    """결과 한 줄과 측정값. 커밋 제목으로는 절대 채우지 않는다 — 기록이 없으면 빈 칸이다."""
    values: list[str] = []
    for item in facts.til:
        if item.result.strip():
            values.append(item.result.strip().splitlines()[0])
        if item.metric:
            values.append(item.metric.text())
    return _capped(values)[:MAX_RESULTS]


def _contribution_bullets(facts: ProjectFacts) -> list[tuple[str, list[EvidenceRef]]]:
    attempts: list[tuple[str, list[EvidenceRef]]] = []
    for item in facts.til:
        for value in item.attempt.splitlines():
            if value.strip():
                attempts.append(
                    (value.strip(), common.til_field_refs("projects_md", item, "attempt"))
                )
    return attempts[:MAX_CONTRIBUTIONS]


def _capped(values) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))


def _fill_text() -> str:
    return common.FILL_IN.removeprefix("> ")


def _used_in(skill: SkillFact, featured: list[str]) -> bool:
    return any(repo in skill.repos for repo in featured)


def _repo_label(repo: str, featured: list[str]) -> str:
    short = repo.rsplit("/", 1)[-1]
    return repo if sum(item.rsplit("/", 1)[-1] == short for item in featured) > 1 else short
