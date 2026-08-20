"""First-pass project sheets. Facts only, no LLM, no portfolio intro."""

from __future__ import annotations

from app.contracts import Evidence, ProjectFacts, TilFact
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

    lead = (project.readme_lead or "").strip()
    description = (project.description or "").strip()
    if lead and lead != description:
        parts.extend(["", "## 개요", "", lead])
    if project.readme_dirs:
        parts.extend(["", "## 구성", ""])
        parts.extend(f"- {name}" for name in project.readme_dirs)
    if project.readme_sections:
        parts.extend(["", "## 섹션", ""])
        parts.extend(f"- {title}" for title in project.readme_sections)
    if project.layout:
        parts.extend(["", "## 구성 파일", ""])
        parts.extend(f"- {name}" for name in project.layout)

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

    learned: list[str] = []
    for item in project.til:
        title = (item.title or "").strip()
        if not title:
            continue
        learned.append(f"- {item.date:%Y-%m-%d} · {title}")
        learned.extend(f"  - {line}" for line in til_excerpt(item.body_markdown))
    if learned:
        parts.extend(["", "## 배운 것", ""])
        parts.extend(learned)

    text = "\n".join(parts).rstrip() + "\n"
    if DATE_MARKER in text:
        text = text.replace(DATE_MARKER, "기간:")
    return text


def til_excerpt(body: str, *, limit: int = 2, max_chars: int = 80) -> list[str]:
    lines: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or DATE_MARKER in line:
            continue
        if line.startswith(">"):
            line = line[1:].strip()
        if line.startswith("- "):
            line = line[2:].strip()
        if len(line) < 8:
            continue
        lines.append(line[:max_chars])
        if len(lines) >= limit:
            break
    return lines


def hub_tail(unmatched: list[TilFact]) -> str:
    rows = [
        f"- {item.date:%Y-%m-%d} · {item.title.strip()}"
        for item in unmatched
        if (item.title or "").strip()
    ]
    if not rows:
        return ""
    return "## 그 외 학습\n" + "\n".join(rows) + "\n"


def _label(repo: str, featured: list[str]) -> str:
    short = repo.rsplit("/", 1)[-1]
    if sum(1 for item in featured if item.rsplit("/", 1)[-1] == short) > 1:
        return repo
    return short
