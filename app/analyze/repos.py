"""Which repositories belong in a document, and in what order."""

from __future__ import annotations

from app.contracts import ProjectFacts, RepoActivity


def eligible(repo: RepoActivity) -> bool:
    """Forks and archived repositories are not evidence of authored work."""
    return not repo.fork and not repo.archived


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
