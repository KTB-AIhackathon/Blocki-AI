"""Which repositories belong in a document, and in what order."""

from __future__ import annotations

import math
import os
import re
from datetime import datetime

from app.contracts import ProjectFacts, RepoActivity, as_utc

_RECENT_DAYS = 90
_YEAR_DAYS = 365
_AWARD = re.compile(r"수상|우수상|대상|최우수|입상|1위|2위|award|winner", re.IGNORECASE)
_NON_PROJECT_PARTS = {"study", "til", "practice", "tutorial", "notes", "log"}


def excluded_repos() -> set[str]:
    """`owner/name` the operator never wants in a document, comma separated.

    Some repositories are dead, private-by-intent or simply not the person's
    work, and no signal in the data says so. Unset means exclude nothing.
    """
    raw = os.environ.get("BLOCKI_EXCLUDE_REPOS", "")
    return {part.strip().casefold() for part in raw.split(",") if part.strip()}


def eligible(repo: RepoActivity) -> bool:
    """Forks and archived repositories are not evidence of authored work."""
    if repo.full_name.casefold() in excluded_repos():
        return False
    return not repo.fork and not repo.archived


def _score(facts: ProjectFacts, repo: RepoActivity, *, now: datetime) -> float:
    facts.score_breakdown = _score_breakdown(facts, repo, now=now)
    return round(sum(facts.score_breakdown.values()), 3)


def _score_breakdown(facts: ProjectFacts, repo: RepoActivity, *, now: datetime) -> dict[str, float]:
    breakdown = {
        "commits": math.log1p(max(facts.my_commits, 0)) * 2.0,
        "prs": facts.merged_prs * 3.0,
        "issues": float(facts.closed_issues),
        "stars": min(repo.stars, 100) * 0.5,
        "team": 5.0 if facts.contributors > 1 else 0.0,
        "duration": 0.0,
        "til": len(facts.til) * 4.0,
        "penalty": 0.0,
        "award": 0.0,
        "recency": 0.0,
        "description": 1.0 if facts.description else 0.0,
    }
    if facts.started_at and facts.ended_at:
        breakdown["duration"] = max((facts.ended_at - facts.started_at).days, 0) / 30.0

    name = repo.name.casefold()
    if name == repo.owner.casefold() or any(
        part in _NON_PROJECT_PARTS for part in re.split(r"[^a-z0-9]+", name) if part
    ) or name.endswith("-log"):
        breakdown["penalty"] = -10.0

    text = " ".join(
        part
        for part in (repo.description or "", repo.readme.content if repo.readme else "")
        if part
    )
    if _AWARD.search(text):
        breakdown["award"] = 10.0

    last = facts.ended_at or as_utc(repo.pushed_at)
    if last is not None:
        days = (now - last).days
        if days <= _RECENT_DAYS:
            breakdown["recency"] = 10.0
        elif days <= _YEAR_DAYS:
            breakdown["recency"] = 4.0
    return breakdown


def award_of(repo: RepoActivity) -> str | None:
    text = "\n".join(
        part for part in (repo.description, repo.readme.content if repo.readme else None) if part
    )
    for line in text.splitlines():
        if _AWARD.search(line):
            return line.strip().lstrip("#- ")[:160]
    return None


def select(
    facts: list[ProjectFacts], *, limit: int, require_own_commits: bool
) -> tuple[list[ProjectFacts], list[str]]:
    warnings: list[str] = []
    candidates = list(facts)

    if require_own_commits and any(f.my_commits > 0 for f in candidates):
        dropped = [f.repo for f in candidates if f.my_commits == 0]
        if dropped:
            candidates = [f for f in candidates if f.my_commits > 0]
            warnings.append(f"본인 커밋이 없어 제외: {', '.join(sorted(dropped))}")

    candidates.sort(key=lambda f: (-f.score, f.repo))
    if len(candidates) > limit:
        warnings.append(f"상위 {limit}개만 사용 (후보 {len(candidates)}개)")
    return candidates[:limit], warnings
