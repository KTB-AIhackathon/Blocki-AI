"""Snapshot to Evidence. Pure: no network, no LLM, no template.

Both document pipelines share this layer so that a portfolio and a resume
built from the same snapshot can never disagree about the facts.
"""

from __future__ import annotations

from datetime import datetime

from app.analyze import projects, repos, skills
from app.contracts import Evidence, GitHubSnapshot, ViewerIdentity, utcnow

__all__ = ["analyze", "projects", "repos", "skills"]

DEFAULT_MAX_PROJECTS = 5


def analyze(
    snapshot: GitHubSnapshot,
    *,
    max_projects: int = DEFAULT_MAX_PROJECTS,
    require_own_commits: bool = True,
    max_highlights: int = 5,
    now: datetime | None = None,
) -> Evidence:
    moment = now or utcnow()
    viewer = _viewer(snapshot)
    usable = [r for r in snapshot.repos if repos.eligible(r)]
    excluded = [r.full_name for r in snapshot.repos if not repos.eligible(r)]

    facts = [
        projects.facts_of(repo, viewer, now=moment, max_highlights=max_highlights)
        for repo in usable
    ]
    selected, warnings = repos.select(
        facts, limit=max_projects, require_own_commits=require_own_commits
    )
    if excluded:
        warnings.append(f"fork/archived 제외: {', '.join(sorted(excluded))}")
    if viewer.login is None and any(f.total_commits for f in facts):
        warnings.append("GitHub 사용자 식별 실패로 본인 커밋을 구분하지 못했습니다")

    chosen = {f.repo for f in selected}
    activity = [r for r in usable if r.full_name in chosen]
    catalogue = skills.extract(activity)
    _attach_languages(selected, catalogue)

    starts = [f.started_at for f in selected if f.started_at]
    ends = [f.ended_at for f in selected if f.ended_at]
    return Evidence(
        viewer=viewer,
        projects=selected,
        skills=catalogue,
        period_start=min(starts) if starts else None,
        period_end=max(ends) if ends else None,
        my_commits=sum(f.my_commits for f in selected),
        complete=snapshot.complete,
        warnings=[*snapshot.warnings, *warnings],
    )


def _viewer(snapshot: GitHubSnapshot) -> ViewerIdentity:
    login = snapshot.viewer_login
    return ViewerIdentity(login=login, aliases=[login.casefold()] if login else [])


def _attach_languages(selected, catalogue) -> None:
    for facts in selected:
        facts.languages = [
            skill
            for skill in catalogue
            if skill.category == "language" and facts.repo in skill.repos
        ]
