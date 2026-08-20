"""Per-repository facts. Counting and grouping only, never inference."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from typing import Literal, TypeVar

from app.contracts import (
    ChangeType,
    CommitFact,
    CommitSummary,
    ProjectFacts,
    RepoActivity,
    ViewerIdentity,
    WorkItem,
    as_utc,
)

_CONVENTIONAL = re.compile(
    r"^(feat|feature|fix|bugfix|perf|refactor|test|tests|docs|doc|build|chore|ci|style)"
    r"(\([^)]*\))?!?\s*:\s*(?P<subject>.+)$",
    re.IGNORECASE,
)
_ALIASES: dict[str, ChangeType] = {
    "feat": "feat",
    "feature": "feat",
    "fix": "fix",
    "bugfix": "fix",
    "perf": "perf",
    "refactor": "refactor",
    "test": "test",
    "tests": "test",
    "docs": "docs",
    "doc": "docs",
    "build": "build",
    "chore": "build",
    "ci": "build",
    "style": "other",
}
_KEYWORDS: tuple[tuple[ChangeType, tuple[str, ...]], ...] = (
    ("fix", ("버그", "수정", "오류", "fix", "hotfix", "patch")),
    ("perf", ("성능", "최적화", "optimiz", "perf", "speed", "캐시", "cache")),
    ("refactor", ("리팩", "구조 개선", "refactor", "cleanup", "정리")),
    ("test", ("테스트", "test", "spec")),
    # `.md` 까지 문서로 본다. "Update 00_대회규정.md" 처럼 파일 이름만 바뀐 커밋이
    # 기여로 실리던 것을 막는다.
    ("docs", ("문서", "readme", "docs", "주석", ".md", "license", "changelog")),
    ("build", ("배포", "빌드", "deploy", "docker", "ci", "설정")),
    ("feat", ("추가", "구현", "신규", "개발", "add", "implement", "support", "introduce")),
)
# What a reader of a portfolio actually wants to see, most interesting first.
_HIGHLIGHT_ORDER: tuple[ChangeType, ...] = ("feat", "perf", "fix", "refactor", "build")
_RECENT_DAYS = 90
_YEAR_DAYS = 365
_MAX_WORK_ITEMS = 3


def facts_of(
    repo: RepoActivity, viewer: ViewerIdentity, *, now: datetime, max_highlights: int = 5
) -> ProjectFacts:
    mine = _mine(repo.commits, viewer)
    dates = sorted(d for d in (as_utc(c.committed_at) for c in mine) if d is not None)
    authors = {
        (c.author or "").strip().casefold() for c in repo.commits if (c.author or "").strip()
    }
    facts = ProjectFacts(
        id=f"repo:{repo.full_name}",
        repo=repo.full_name,
        url=repo.html_url or f"https://github.com/{repo.full_name}",
        description=(repo.description or "").strip() or None,
        topics=[t for t in repo.topics if t.strip()],
        started_at=dates[0] if dates else None,
        ended_at=dates[-1] if dates else None,
        my_commits=len(mine),
        total_commits=len(repo.commits),
        contributors=max(len(authors), 1),
        merged_prs=len(_mine_by_author(
            [pr for pr in repo.pull_requests if pr.merged], viewer, lambda pr: [pr.author])),
        closed_issues=len(_mine_by_author(
            [i for i in repo.issues if i.state.casefold() == "closed"],
            viewer,
            lambda issue: [issue.author, *issue.assignees],
        )),
        highlights=_highlights(repo.full_name, mine, max_highlights),
        pull_requests=_owned_work(
            [pr for pr in repo.pull_requests if pr.merged],
            viewer,
            repo.full_name,
            "pr",
            lambda pr: [pr.author],
        ),
        issues=_owned_work(
            [issue for issue in repo.issues if issue.state.casefold() == "closed"],
            viewer,
            repo.full_name,
            "issue",
            lambda issue: [issue.author, *issue.assignees],
        ),
    )
    facts.score = _score(facts, repo, now=now)
    return facts


def _mine(commits: list[CommitSummary], viewer: ViewerIdentity) -> list[CommitSummary]:
    """Commits we can defend as the user's own.

    Collect already marked each one, so this only decides what to do when the
    marking produced nothing.
    """
    owned = [c for c in commits if c.mine]
    if owned:
        return owned
    return _fallback(commits, viewer, lambda c: [c.author, c.author_email])


T = TypeVar("T")


def _mine_by_author(
    items: list[T], viewer: ViewerIdentity, names: Callable[[T], list[str | None]]
) -> list[T]:
    """The subset the user can claim, by whatever names each item carries.

    A team repository's merged PRs and closed issues are mostly other people's.
    Counting them all would put their work on this user's resume.
    """
    owned = [item for item in items if any(viewer.owns(name) for name in names(item))]
    if owned:
        return owned
    return _fallback(items, viewer, names)


def _fallback(
    items: list[T], viewer: ViewerIdentity, names: Callable[[T], list[str | None]]
) -> list[T]:
    """What to report when nothing matched the viewer.

    An empty result is only honest when attribution was possible and came back
    negative. Without a viewer, or without any names on the data, we cannot tell
    the user's work from anyone else's, and silently reporting zero would be a
    worse lie than reporting the total.
    """
    if not items:
        return []
    if not viewer.aliases:
        return items
    if not any(name for item in items for name in names(item)):
        return items
    return []


def _owned_work(
    items: list[T],
    viewer: ViewerIdentity,
    repo: str,
    source_type: Literal["pr", "issue"],
    names: Callable[[T], list[str | None]],
) -> list[WorkItem]:
    """Titles the user can defend. No fallback — a stranger's PR is not a highlight."""
    owned = [item for item in items if any(viewer.owns(name) for name in names(item))]
    owned.sort(key=lambda item: (-_epoch(as_utc(getattr(item, "updated_at", None))), -int(item.number)))
    out: list[WorkItem] = []
    for item in owned[:_MAX_WORK_ITEMS]:
        title = (getattr(item, "title", None) or "").strip()
        if not title:
            continue
        out.append(
            WorkItem(
                id=f"{source_type}:{repo}#{item.number}",
                repo=repo,
                number=int(item.number),
                title=title,
                source_type=source_type,
            )
        )
    return out


def _highlights(repo: str, commits: list[CommitSummary], limit: int) -> list[CommitFact]:
    facts: list[CommitFact] = []
    seen: set[str] = set()
    for commit in commits:
        subject, change_type = classify(commit.message)
        # README 와 문서 커밋은 근거로 쓰지 않는다. 어느 문서에도, LLM 프롬프트에도
        # 들어가면 안 되므로 여기서 한 번만 걸러 낸다.
        if not subject or change_type == "docs":
            continue
        key = subject.casefold()
        if key in seen:
            continue
        seen.add(key)
        facts.append(
            CommitFact(
                id=f"commit:{commit.sha}",
                repo=repo,
                sha=commit.sha,
                subject=subject,
                change_type=change_type,
                committed_at=as_utc(commit.committed_at),
            )
        )
    facts.sort(key=lambda f: (_rank(f.change_type), -_epoch(f.committed_at), f.subject))
    return facts[:limit]


def classify(message: str) -> tuple[str, ChangeType]:
    first = (message or "").splitlines()[0].strip() if message else ""
    if not first:
        return "", "other"
    match = _CONVENTIONAL.match(first)
    if match:
        prefix = match.group(1).lower()
        return match.group("subject").strip(), _ALIASES.get(prefix, "other")
    lowered = first.casefold()
    for change_type, needles in _KEYWORDS:
        if any(needle in lowered for needle in needles):
            return first, change_type
    return first, "other"


def _rank(change_type: ChangeType) -> int:
    try:
        return _HIGHLIGHT_ORDER.index(change_type)
    except ValueError:
        return len(_HIGHLIGHT_ORDER)


def _epoch(value: datetime | None) -> float:
    return value.timestamp() if value else 0.0


def _score(facts: ProjectFacts, repo: RepoActivity, *, now: datetime) -> float:
    score = facts.my_commits * 2.0 + facts.merged_prs * 3.0 + facts.closed_issues
    score += min(repo.stars, 100) * 0.5
    last = facts.ended_at or as_utc(repo.pushed_at)
    if last is not None:
        days = (now - last).days
        if days <= _RECENT_DAYS:
            score += 10.0
        elif days <= _YEAR_DAYS:
            score += 4.0
    if facts.description:
        score += 1.0
    return round(score, 3)
