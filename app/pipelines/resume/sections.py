"""Resume section rendering from repository and TIL evidence."""

from __future__ import annotations

from collections import defaultdict

from app.analyze import skills as skill_analysis
from app.contracts import Evidence, EvidenceRef, ProjectFacts, SkillFact
from app.pipelines import common

Section = tuple[str, list[EvidenceRef]]
MAX_CARDS = 4
MAX_CONTRIBUTIONS = 4


def summary(evidence: Evidence, lines: list[str] | None = None) -> Section:
    text = [line.strip() for line in (lines or []) if line.strip()]
    if not text:
        return "", []
    refs = [common.project_ref("summary_md", project) for project in evidence.projects]
    return "\n\n".join(text), refs


def skills(evidence: Evidence, readme_skills: str) -> Section:
    if not readme_skills.strip():
        return common.FILL_IN, []
    return readme_skills.strip(), []


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
    facts = facts.model_copy(
        update={"til": [item for item in facts.til if item.attempt.strip() or item.result.strip()]}
    )
    refs = [common.project_ref("projects_md", facts)]
    name = _repo_label(facts.repo, featured)
    description = (facts.description or "").strip()
    heading = f"### {number}. {name}"
    if description:
        heading += f" — {description}"
    lines = [heading, "", f"- **문제**: {_til_text(facts, 'problem')}", f"- **목표**: {_til_text(facts, 'goal')}"]
    lines.append(f"- **기여**: {common.contribution(facts)}")
    for bullet, bullet_refs in _contribution_bullets(facts):
        lines.append(f"  - {bullet}")
        refs.extend(bullet_refs)
    lines.append(f"- **성과**: {_result_text(facts)}")

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


def _til_text(facts: ProjectFacts, field: str) -> str:
    values = [getattr(item, field).strip() for item in facts.til if getattr(item, field).strip()]
    return "\n".join(dict.fromkeys(value for value in values if value)) or _fill_text()


def _result_text(facts: ProjectFacts) -> str:
    values: list[str] = []
    for item in facts.til:
        if item.result.strip():
            values.append(item.result.strip())
        if item.metric:
            values.append(item.metric.text())
    return "\n".join(dict.fromkeys(values)) or _fill_text()


def _contribution_bullets(facts: ProjectFacts) -> list[tuple[str, list[EvidenceRef]]]:
    if facts.til:
        attempts: list[tuple[str, list[EvidenceRef]]] = []
        for item in facts.til:
            for value in item.attempt.splitlines():
                if value.strip():
                    attempts.append(
                        (value.strip(), common.til_field_refs("projects_md", item, "attempt"))
                    )
        return attempts[:MAX_CONTRIBUTIONS]

    grouped: dict[str, int] = defaultdict(int)
    labels = {
        "feat": "기능 구현",
        "fix": "버그 수정",
        "perf": "성능 개선",
        "refactor": "구조 개선",
        "test": "테스트 보강",
        "docs": "문서화",
        "build": "배포·설정",
        "other": "기타 작업",
    }
    for item in facts.highlights:
        grouped[item.change_type] += 1
    refs_by_type = defaultdict(list)
    for item in facts.highlights:
        refs_by_type[item.change_type].append(common.commit_ref("projects_md", item))
    out: list[tuple[str, list[EvidenceRef]]] = []
    for change_type, count in grouped.items():
        out.append((f"{labels.get(change_type, '기타 작업')} 관련 작업 {count}건", refs_by_type[change_type]))
        if len(out) >= MAX_CONTRIBUTIONS:
            break
    return out


def _fill_text() -> str:
    return common.FILL_IN.removeprefix("> ")


def _repo_label(repo: str, featured: list[str]) -> str:
    short = repo.rsplit("/", 1)[-1]
    return repo if sum(item.rsplit("/", 1)[-1] == short for item in featured) > 1 else short
