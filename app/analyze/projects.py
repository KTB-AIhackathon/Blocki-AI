"""Per-repository facts. Counting and grouping only, never inference."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from typing import Any, Literal, TypeVar

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
_BOOTSTRAP = re.compile(
    r"^(init|initial commit|first commit|initial|초기\s*커밋|프로젝트\s*생성)\.?$",
    re.IGNORECASE,
)
_URLISH = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]+\)")
_MARKUP = re.compile(r"[*_`<>#\[\]()]+")
_CODE_FENCE = {"bash", "sh", "zsh", "shell", "powershell", "ps1", "python", "py", "js", "ts", "json", "yaml", "yml"}
_DIR_NAME = re.compile(r"^[\w.\-]+$")
_LEAK_TAIL = re.compile(r"[\"']\}+$")


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
        readme_lead=readme_lead(repo.readme.content if repo.readme else None),
        readme_sections=readme_sections(repo.readme.content if repo.readme else None),
        readme_dirs=readme_dirs(repo.readme.content if repo.readme else None),
        layout=_layout(repo.manifest_files),
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


def readme_lead(content: str | None, *, max_chars: int = 280) -> str | None:
    """First real README paragraph. Quotes and link-only lines lose to body text."""
    if not content:
        return None
    bodies: list[str] = []
    quotes: list[str] = []
    chunks: list[str] = []
    in_quote = False
    in_fence = False

    def flush() -> None:
        nonlocal chunks
        text = " ".join(chunks).strip()
        chunks = []
        if text:
            (quotes if in_quote else bodies).append(text)

    for raw in content.splitlines():
        if raw.startswith("```"):
            if chunks:
                flush()
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        line = raw.strip()
        if (
            not line
            or line.startswith("#")
            or line.startswith("<!--")
            or line.startswith("[!")
            or line.startswith("![")
        ):
            if chunks:
                flush()
            continue
        quoted = line.startswith(">")
        if quoted:
            line = line[1:].strip()
            if not line:
                if chunks:
                    flush()
                continue
        if _pointer(line):
            if chunks:
                flush()
            continue
        if chunks and quoted != in_quote:
            flush()
        if not chunks:
            in_quote = quoted
        chunks.append(line)
        joined = " ".join(chunks)
        if len(joined) >= max_chars:
            chunks = [joined[:max_chars].rstrip()]
            flush()
            break
    else:
        flush()

    for text in (*bodies, *quotes):
        if text:
            return text[:max_chars]
    return None


def readme_sections(content: str | None, *, limit: int = 8) -> list[str]:
    if not content:
        return []
    titles: list[str] = []
    for raw in content.splitlines():
        line = raw.strip()
        if not line.startswith("## ") or line.startswith("###"):
            continue
        title = line[3:].strip().lstrip("#").strip()
        if not title or title in titles:
            continue
        titles.append(title)
        if len(titles) >= limit:
            break
    return titles


def readme_dirs(content: str | None, *, limit: int = 8) -> list[str]:
    """Top-level names from the first directory-looking fence. No rewriting."""
    if not content:
        return []
    in_fence = False
    skip_lang = False
    names: list[str] = []
    for raw in content.splitlines():
        if raw.startswith("```"):
            if in_fence:
                if len(names) >= 2:
                    return names[:limit]
                names = []
                in_fence = False
                continue
            in_fence = True
            lang = raw[3:].strip().split()[0].lower() if raw[3:].strip() else ""
            skip_lang = lang in _CODE_FENCE
            names = []
            continue
        if not in_fence or skip_lang:
            continue
        if raw.startswith((" ", "\t")):
            continue
        token = raw.strip().split()[0].rstrip("/") if raw.strip() else ""
        if not token or not _DIR_NAME.match(token) or token in names:
            continue
        names.append(token)
    if len(names) >= 2:
        return names[:limit]
    return []


def _pointer(line: str) -> bool:
    has_url = bool(_URLISH.search(line) or _MD_LINK.search(line) or "<http" in line)
    leftover = _MD_LINK.sub(r"\1", line)
    leftover = _URLISH.sub("", leftover)
    leftover = _MARKUP.sub(" ", leftover)
    leftover = re.sub(r"\s+", " ", leftover).strip(" :-—·|")
    if not leftover:
        return True
    return has_url and len(leftover) < 40


def _layout(paths: list[Any], *, limit: int = 8) -> list[str]:
    seen: list[str] = []
    for path in paths:
        name = _file_name(path)
        if not name or name in seen:
            continue
        seen.append(name)
        if len(seen) >= limit:
            break
    return seen


def _file_name(item: Any) -> str:
    """Basename of a contents row. Drops `str(dict)` leftovers like `.gitignore'}}`."""
    if isinstance(item, dict):
        raw = str(item.get("name") or item.get("path") or "").strip()
    else:
        raw = str(item or "").strip()
    name = raw.rsplit("/", 1)[-1].split("?", 1)[0]
    return _LEAK_TAIL.sub("", name).strip()


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
        if not subject or change_type == "docs" or _BOOTSTRAP.match(subject):
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
