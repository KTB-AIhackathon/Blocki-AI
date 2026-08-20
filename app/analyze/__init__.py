"""Snapshot to Evidence. Pure: no network, no LLM, no template.

Both document pipelines share this layer so that a portfolio and a resume
built from the same snapshot can never disagree about the facts.
"""

from __future__ import annotations

from datetime import datetime

from app.analyze import join, projects, repos, skills
from app.analyze.til import facts_of as til_facts_of
from app.contracts import Evidence, GitHubSnapshot, NotionSnapshot, ViewerIdentity, utcnow

__all__ = ["analyze", "join", "projects", "repos", "skills"]

DEFAULT_MAX_PROJECTS = 3


def analyze(
    snapshot: GitHubSnapshot,
    *,
    til: NotionSnapshot | None = None,
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
    til_facts = til_facts_of(til) if til is not None else []
    if til is not None:
        join.attach(facts, til_facts)
    for facts_of_repo, repo in zip(facts, usable):
        facts_of_repo.score = repos._score(facts_of_repo, repo, now=moment)
        facts_of_repo.award = repos.award_of(repo)
    selected, warnings = repos.select(
        facts, limit=max_projects, require_own_commits=require_own_commits
    )
    unmatched_til = join.attach(selected, til_facts) if til is not None else []
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
    evidence = Evidence(
        viewer=viewer,
        projects=selected,
        skills=catalogue,
        period_start=min(starts) if starts else None,
        period_end=max(ends) if ends else None,
        my_commits=sum(f.my_commits for f in selected),
        complete=snapshot.complete,
        warnings=[*snapshot.warnings, *warnings],
        til=til_facts,
        unmatched_til=unmatched_til,
        selection_candidates=facts,
    )
    if til is None:
        return evidence
    evidence.complete = evidence.complete and til.complete
    evidence.warnings.extend(til.warnings)
    return evidence


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
