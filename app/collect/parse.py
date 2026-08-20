"""Shape-tolerant parsing of MCP payloads.

The GitHub MCP server returns JSON, JSON-in-text-blocks, or plain prose
depending on the tool. Everything here normalises those shapes; nothing here
performs I/O.
"""

from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timezone
from typing import Any

from app.contracts import (
    CommitSummary,
    IssueSummary,
    LanguageShare,
    PrSummary,
    ReadmeBlob,
    RepoRef,
)

README_PATH = "README.md"
MANIFESTS = frozenset(
    {
        "package.json",
        "pyproject.toml",
        "requirements.txt",
        "go.mod",
        "Cargo.toml",
        "pom.xml",
        "Gemfile",
        "composer.json",
        "build.gradle",
        "build.gradle.kts",
        "Dockerfile",
        "docker-compose.yml",
    }
)

_STATUS_RE = re.compile(r"\b(401|403|404|429)\b")
_LIST_KEYS = (
    "items",
    "commits",
    "issues",
    "pull_requests",
    "pullRequests",
    "repositories",
    "data",
    "result",
    "content",
    "entries",
    "files",
)


def jsonish(raw: Any) -> Any:
    if raw is None or isinstance(raw, (int, float, bool, dict)):
        return raw
    if isinstance(raw, list):
        if raw and all(isinstance(x, dict) and "text" in x for x in raw):
            parsed = _parse_text_blocks(raw)
            if parsed is not None:
                return parsed
            return jsonish("".join(str(x.get("text") or "") for x in raw))
        return raw
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    if not isinstance(raw, str):
        return raw
    text = raw.strip()
    if not text:
        return None
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return raw.strip()


def _parse_text_blocks(blocks: list[dict[str, Any]]) -> Any:
    parsed: list[Any] = []
    for block in blocks:
        text = str(block.get("text") or "").strip()
        if not text:
            continue
        try:
            parsed.append(json.loads(text))
        except json.JSONDecodeError:
            return None
    if not parsed:
        return None
    return parsed[0] if len(parsed) == 1 else parsed


def as_dict(raw: Any) -> dict[str, Any]:
    value = jsonish(raw)
    return value if isinstance(value, dict) else {}


def as_list(raw: Any) -> list[Any]:
    value = jsonish(raw)
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in _LIST_KEYS:
            inner = value.get(key)
            if isinstance(inner, list):
                return inner
        if value.get("sha") or value.get("number") or value.get("full_name"):
            return [value]
    return []


def first_dict(rows: list[Any]) -> dict[str, Any] | None:
    for item in rows:
        if isinstance(item, dict):
            return item
    return None


def http_status(exc: BaseException | None, text: str) -> int | None:
    for cur in _walk_exceptions(exc):
        for attr in ("status_code", "status"):
            value = getattr(cur, attr, None)
            if isinstance(value, int) and value > 0:
                return value
        response = getattr(cur, "response", None)
        if response is not None:
            value = getattr(response, "status_code", None)
            if isinstance(value, int) and value > 0:
                return value
    match = _STATUS_RE.search(text)
    if match:
        return int(match.group(1))
    low = text.lower()
    if "unauthorized" in low or "bad credentials" in low:
        return 401
    if "rate limit" in low or "too many requests" in low:
        return 429
    # What GitHub says when the token is valid but the scope is too narrow.
    if "forbidden" in low or "not accessible" in low or "insufficient scope" in low:
        return 403
    return None


def _walk_exceptions(exc: BaseException | None) -> list[BaseException]:
    found: list[BaseException] = []
    seen: set[int] = set()
    stack = [exc]
    while stack and len(found) < 16:
        cur = stack.pop()
        if cur is None or id(cur) in seen:
            continue
        seen.add(id(cur))
        found.append(cur)
        stack.append(cur.__cause__)
        stack.append(cur.__context__)
        group = getattr(cur, "exceptions", None)
        if group:
            stack.extend(group)
    return found


def text_of(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        inner = value.get("login") or value.get("name") or value.get("email")
        return str(inner) if inner is not None else None
    text = str(value)
    return text or None


def iso(value: datetime) -> str:
    dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def when(value: Any) -> datetime | None:
    """Never raise on a timestamp: a malformed date must not drop a repo."""
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def login_of(raw: Any) -> str | None:
    if isinstance(raw, str) and raw:
        return raw
    data = as_dict(raw)
    nested = data.get("data") if isinstance(data.get("data"), dict) else {}
    login = data.get("login") or data.get("username") or nested.get("login")
    return str(login) if login else None


def repo_ref(item: Any) -> RepoRef | None:
    if isinstance(item, RepoRef):
        return item
    if isinstance(item, str) and "/" in item:
        owner, name = item.split("/", 1)
        return RepoRef(owner=owner, name=name)
    if not isinstance(item, dict):
        return None
    owner = item.get("owner")
    if isinstance(owner, dict):
        owner = owner.get("login") or owner.get("name")
    name = item.get("name") or item.get("repo")
    if not owner or not name:
        full = item.get("full_name") or item.get("fullName")
        if isinstance(full, str) and "/" in full:
            owner, name = full.split("/", 1)
    return RepoRef(owner=str(owner), name=str(name)) if owner and name else None


def repo_refs(raw: Any) -> list[RepoRef]:
    out: list[RepoRef] = []
    for item in as_list(raw):
        ref = repo_ref(item)
        if ref is not None:
            out.append(ref)
    return out


def languages(raw: Any) -> list[LanguageShare]:
    out: list[LanguageShare] = []
    if isinstance(raw, dict):
        for name, nbytes in raw.items():
            try:
                out.append(LanguageShare(name=str(name), bytes=int(nbytes)))
            except (TypeError, ValueError):
                continue
        return out
    for item in raw or []:
        if isinstance(item, str):
            out.append(LanguageShare(name=item, bytes=0))
        elif isinstance(item, dict) and item.get("name"):
            try:
                nbytes = int(item.get("bytes") or 0)
            except (TypeError, ValueError):
                nbytes = 0
            out.append(LanguageShare(name=str(item["name"]), bytes=nbytes))
    return out


def commits_raw(raw: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in as_list(raw):
        if not isinstance(item, dict):
            continue
        commit = item.get("commit") if isinstance(item.get("commit"), dict) else {}
        commit_author = commit.get("author") if isinstance(commit.get("author"), dict) else {}
        author = item.get("author") or commit.get("author")
        rows.append(
            {
                "sha": item.get("sha"),
                "message": item.get("message") or commit.get("message") or "",
                "author": author,
                "author_email": commit_author.get("email") or item.get("author_email"),
                "committed_at": (
                    item.get("committed_at")
                    or item.get("date")
                    or commit.get("committed_at")
                    or (commit.get("committer") or {}).get("date")
                    or commit_author.get("date")
                ),
            }
        )
    return rows


def commits(raw: Any) -> list[CommitSummary]:
    out: list[CommitSummary] = []
    for item in commits_raw(raw):
        sha = item.get("sha")
        if not sha:
            continue
        out.append(
            CommitSummary(
                sha=str(sha),
                message=str(item.get("message") or ""),
                author=text_of(item.get("author")),
                author_email=text_of(item.get("author_email")),
                committed_at=when(item.get("committed_at")),
            )
        )
    return out


def issues_raw(raw: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in as_list(raw):
        if not isinstance(item, dict) or item.get("pull_request"):
            continue
        rows.append(
            {
                "number": item.get("number") or item.get("issue_number"),
                "title": item.get("title") or "",
                "state": item.get("state") or "",
                "author": item.get("user") or item.get("author"),
                "assignees": item.get("assignees") or item.get("assignee"),
                "updated_at": item.get("updated_at") or item.get("updatedAt"),
            }
        )
    return rows


def issues(raw: Any) -> list[IssueSummary]:
    out: list[IssueSummary] = []
    for item in issues_raw(raw):
        if item.get("number") is None:
            continue
        out.append(
            IssueSummary(
                number=int(item["number"]),
                title=str(item.get("title") or ""),
                state=str(item.get("state") or ""),
                author=text_of(item.get("author")),
                assignees=logins(item.get("assignees")),
                updated_at=when(item.get("updated_at")),
            )
        )
    return out


def logins(value: Any) -> list[str]:
    """GitHub sends assignees as a list, one object, or nothing at all."""
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    names = (text_of(item) for item in items)
    return [name for name in names if name]


def prs_raw(raw: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in as_list(raw):
        if not isinstance(item, dict):
            continue
        merged = bool(item.get("merged") or item.get("merged_at") or item.get("mergedAt"))
        rows.append(
            {
                "number": item.get("number") or item.get("pullNumber") or item.get("pull_number"),
                "title": item.get("title") or "",
                "state": item.get("state") or "",
                "merged": merged,
                "author": item.get("user") or item.get("author"),
                "updated_at": item.get("updated_at") or item.get("updatedAt"),
            }
        )
    return rows


def prs(raw: Any) -> list[PrSummary]:
    out: list[PrSummary] = []
    for item in prs_raw(raw):
        if item.get("number") is None:
            continue
        out.append(
            PrSummary(
                number=int(item["number"]),
                title=str(item.get("title") or ""),
                state=str(item.get("state") or ""),
                merged=bool(item.get("merged")),
                author=text_of(item.get("author")),
                updated_at=when(item.get("updated_at")),
            )
        )
    return out


def readme_blob(raw: Any) -> ReadmeBlob | None:
    data = as_dict(raw)
    if not data:
        return None
    return ReadmeBlob(
        path=str(data.get("path") or README_PATH),
        blob_sha=str(data.get("blob_sha") or data.get("sha") or ""),
        content=file_content(data),
    )


def file_content(data: dict[str, Any]) -> str:
    content = data.get("content")
    if content is None:
        return ""
    if not isinstance(content, str):
        return "" if isinstance(content, list) else str(content)
    if data.get("encoding") == "base64":
        try:
            return base64.b64decode(content).decode("utf-8", errors="replace")
        except (ValueError, TypeError):
            return content
    return content


def manifest_names(raw: Any) -> list[str]:
    names: list[str] = []
    for item in as_list(raw):
        if isinstance(item, str):
            name = item.rsplit("/", 1)[-1]
        elif isinstance(item, dict):
            name = str(item.get("name") or item.get("path") or "").rsplit("/", 1)[-1]
        else:
            continue
        if name in MANIFESTS and name not in names:
            names.append(name)
    return names
