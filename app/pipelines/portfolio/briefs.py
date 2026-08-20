"""First-pass project sheets. Facts only, no LLM, no portfolio intro."""

from __future__ import annotations

from app.contracts import Evidence, ProjectFacts
from app.pipelines import common

DATE_MARKER = "날짜:"


def render_briefs(evidence: Evidence) -> list[dict[str, str]]:
    featured = [project.repo for project in evidence.projects]
    return [
        {
            "title": _label(project.repo, featured),
            "markdown": brief_of(project, evidence),
        }
        for project in evidence.projects
    ]


def brief_of(project: ProjectFacts, evidence: Evidence) -> str:
    parts = [f"# {_label(project.repo, [p.repo for p in evidence.projects])}", ""]
    if project.description:
        parts.extend([f"> {project.description}", ""])

    meta: list[str] = []
    if project.url:
        meta.append(f"- 저장소: {project.url}")
    span = common.period(project.started_at, project.ended_at)
    if span:
        months = common.duration_months(project.started_at, project.ended_at)
        suffix = f" ({months}개월)" if months is not None else ""
        meta.append(f"- 기간: {span}{suffix}")
    meta.append(f"- 구성: {common.team_label(project)}")
    meta.append(f"- 기여: {common.contribution(project)}")
    stack = [skill.name for skill in evidence.skills if project.repo in skill.repos]
    if stack:
        meta.append(f"- 기술: {', '.join(stack)}")
    parts.extend(meta)

    commits = [item.subject.strip() for item in project.highlights if (item.subject or "").strip()]
    if commits:
        parts.extend(["", "## 커밋", ""])
        parts.extend(f"- {subject}" for subject in commits)

    pulls = [item.title.strip() for item in project.pull_requests if (item.title or "").strip()]
    if pulls:
        parts.extend(["", "## PR", ""])
        parts.extend(f"- {title}" for title in pulls)

    issues = [item.title.strip() for item in project.issues if (item.title or "").strip()]
    if issues:
        parts.extend(["", "## 이슈", ""])
        parts.extend(f"- {title}" for title in issues)

    learned = [
        f"- {item.date:%Y-%m-%d} · {item.title.strip()}"
        for item in project.til
        if (item.title or "").strip()
    ]
    if learned:
        parts.extend(["", "## 배운 것", ""])
        parts.extend(learned)

    text = "\n".join(parts).rstrip() + "\n"
    if DATE_MARKER in text:
        text = text.replace(DATE_MARKER, "기간:")
    return text


def _label(repo: str, featured: list[str]) -> str:
    short = repo.rsplit("/", 1)[-1]
    if sum(1 for item in featured if item.rsplit("/", 1)[-1] == short) > 1:
        return repo
    return short
