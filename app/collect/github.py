from __future__ import annotations

import inspect
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Callable

from app.contracts import (
    CollectRequest,
    CommitSummary,
    GitHubCollectError,
    GitHubSnapshot,
    IssueSummary,
    JobError,
    LanguageShare,
    PrSummary,
    ReadmeBlob,
    RepoActivity,
    RepoCursor,
    RepoRef,
    snapshot_digest_of,
    utcnow,
)

CallTool = Callable[[str, dict[str, Any]], Any]

def mcp_url() -> str:
    return os.environ.get("GITHUB_MCP_URL", "https://api.githubcopilot.com/mcp/")
README_PATH = "README.md"
MAX_REPOS = 5
MAX_COMMITS = 30
MAX_ISSUES = 20
MAX_PRS = 20
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
    }
)

_STATUS_RE = re.compile(r"\b(401|404|429)\b")


async def collect_github(
    req: CollectRequest, github_pat: str, *, call_tool: CallTool | None = None
) -> GitHubSnapshot:
    if call_tool is None:
        try:
            call_tool = await _open_mcp(github_pat)
        except GitHubCollectError:
            raise
        except BaseException as exc:
            raise _as_global(exc, github_pat) from None

    collected_at = utcnow()
    warnings: list[str] = []
    complete = True

    try:
        me = await _call(call_tool, "get_me", {})
    except GitHubCollectError:
        raise
    except BaseException as exc:
        raise _as_global(exc, github_pat) from None

    viewer_login = _login(me)

    refs = list(req.repos)
    if not refs:
        try:
            listed = await _call(call_tool, "list_repos", {})
        except GitHubCollectError:
            raise
        except BaseException as exc:
            raise _as_global(exc, github_pat) from None
        refs = _refs(listed)[:MAX_REPOS]

    cursor_by_repo = {(c.owner, c.name): c for c in (req.cursor or [])}
    needs = set(req.needs)
    repos: list[RepoActivity] = []
    next_cursor: list[RepoCursor] = []

    for ref in refs:
        try:
            activity, cursor = await _collect_repo(
                call_tool,
                ref,
                needs=needs,
                req_since=req.since,
                cursor=cursor_by_repo.get((ref.owner, ref.name)),
                collected_at=collected_at,
                readme_path=req.readme_path or README_PATH,
            )
        except GitHubCollectError:
            raise
        except BaseException as exc:
            err = _as_global(exc, github_pat)
            if err.error.code in ("github_auth", "github_rate_limit"):
                raise err from None
            warnings.append(f"{ref.owner}/{ref.name} skipped")
            complete = False
            continue
        repos.append(activity)
        if cursor is not None:
            next_cursor.append(cursor)

    return GitHubSnapshot(
        collected_at=collected_at,
        complete=complete,
        snapshot_digest=snapshot_digest_of(repos, viewer_login),
        viewer_login=viewer_login,
        repos=repos,
        next_cursor=next_cursor,
        warnings=warnings,
    )


async def _collect_repo(
    call_tool: CallTool,
    ref: RepoRef,
    *,
    needs: set[str],
    req_since: datetime | None,
    cursor: RepoCursor | None,
    collected_at: datetime,
    readme_path: str = README_PATH,
) -> tuple[RepoActivity, RepoCursor | None]:
    meta = _as_dict(await _call(call_tool, "get_repo_meta", {"owner": ref.owner, "name": ref.name}))
    head_sha = _str(meta.get("head_sha") or meta.get("sha"))
    skip_commits = (
        "activity" in needs
        and cursor is not None
        and bool(head_sha)
        and cursor.head_sha == head_sha
    )

    commits: list[CommitSummary] = []
    issues: list[IssueSummary] = []
    prs: list[PrSummary] = []
    if "activity" in needs:
        since = cursor.last_success_at if cursor is not None else req_since
        args: dict[str, Any] = {"owner": ref.owner, "name": ref.name}
        if since is not None:
            args["since"] = _iso(since)
        if not skip_commits:
            commits = _commits(await _call(call_tool, "list_commits", args))[:MAX_COMMITS]
        issues = _issues(await _call(call_tool, "list_issues", args))[:MAX_ISSUES]
        prs = _prs(await _call(call_tool, "list_pull_requests", args))[:MAX_PRS]

    readme = None
    if "readme" in needs:
        try:
            blob = await _call(
                call_tool,
                "get_file",
                {"owner": ref.owner, "name": ref.name, "path": readme_path},
            )
            readme = _readme(blob)
        except GitHubCollectError:
            raise
        except BaseException as exc:
            if _http_status(exc, str(exc)) == 404:
                readme = None
            else:
                raise

    activity = RepoActivity(
        owner=ref.owner,
        name=ref.name,
        default_branch=_str(meta.get("default_branch")),
        head_sha=head_sha,
        description=_str(meta.get("description")),
        topics=[str(t) for t in (meta.get("topics") or []) if t is not None],
        languages=_languages(meta.get("languages")),
        manifest_files=[str(p) for p in (meta.get("manifest_files") or []) if p],
        commits=commits,
        issues=issues,
        pull_requests=prs,
        readme=readme,
    )
    nxt = None
    if head_sha:
        nxt = RepoCursor(
            owner=ref.owner,
            name=ref.name,
            head_sha=head_sha,
            last_success_at=collected_at,
        )
    return activity, nxt


async def _open_mcp(github_pat: str) -> CallTool:
    from langchain_mcp_adapters.client import MultiServerMCPClient

    client = MultiServerMCPClient(
        {
            "github": {
                "transport": "http",
                "url": mcp_url(),
                "headers": {
                    "Authorization": f"Bearer {github_pat}",
                    "X-MCP-Toolsets": "context,repos,issues,pull_requests",
                    "X-MCP-Readonly": "true",
                },
            }
        },
        handle_tool_errors=False,
    )
    tools = {t.name: t for t in await client.get_tools()}
    login_holder: dict[str, str | None] = {"login": None}

    async def invoke(mcp_name: str, args: dict[str, Any]) -> Any:
        tool = tools.get(mcp_name)
        if tool is None:
            raise RuntimeError(f"mcp tool missing: {mcp_name}")
        last: BaseException | None = None
        for attempt in range(3):
            try:
                return _jsonish(await tool.ainvoke(args))
            except BaseException as exc:
                last = exc
                status = _http_status(exc, str(exc))
                if status == 429 and attempt < 2:
                    continue
                raise
        assert last is not None
        raise last

    async def call_tool(name: str, args: dict[str, Any]) -> Any:
        args = args or {}
        if name == "get_me":
            data = _as_dict(await invoke("get_me", {}))
            login = _login(data)
            login_holder["login"] = login
            return {"login": login}
        if name == "list_repos":
            login = login_holder["login"] or _login(await invoke("get_me", {}))
            login_holder["login"] = login
            query = f"user:{login}" if login else "is:public"
            data = await invoke(
                "search_repositories",
                {"query": query, "perPage": MAX_REPOS, "minimal_output": False},
            )
            return [_ref(item).model_dump() for item in _as_list(data) if _ref(item)]
        if name == "get_repo_meta":
            return await _mcp_repo_meta(invoke, args["owner"], args["name"])
        if name == "list_commits":
            payload = {
                "owner": args["owner"],
                "repo": args["name"],
                "perPage": MAX_COMMITS,
            }
            if args.get("since"):
                payload["since"] = args["since"]
            return _commits_raw(await invoke("list_commits", payload))
        if name == "list_issues":
            payload = {
                "owner": args["owner"],
                "repo": args["name"],
                "perPage": MAX_ISSUES,
                "state": "all",
            }
            if args.get("since"):
                payload["since"] = args["since"]
            return _issues_raw(await invoke("list_issues", payload))
        if name == "list_pull_requests":
            payload = {
                "owner": args["owner"],
                "repo": args["name"],
                "perPage": MAX_PRS,
                "state": "all",
                "sort": "updated",
                "direction": "desc",
            }
            rows = _prs_raw(await invoke("list_pull_requests", payload))
            since = args.get("since")
            if since:
                rows = [r for r in rows if str(r.get("updated_at") or "") >= str(since)]
            return rows
        if name == "get_file":
            try:
                data = _as_dict(
                    await invoke(
                        "get_file_contents",
                        {
                            "owner": args["owner"],
                            "repo": args["name"],
                            "path": args.get("path") or README_PATH,
                        },
                    )
                )
            except BaseException as exc:
                if _http_status(exc, str(exc)) == 404:
                    return None
                raise
            return {
                "path": data.get("path") or args.get("path") or README_PATH,
                "blob_sha": data.get("sha") or data.get("blob_sha") or "",
                "content": _file_content(data),
            }
        raise RuntimeError(f"unknown logical tool: {name}")

    return call_tool


async def _mcp_repo_meta(invoke, owner: str, name: str) -> dict[str, Any]:
    info: dict[str, Any] = {
        "default_branch": None,
        "head_sha": None,
        "description": None,
        "topics": [],
        "languages": [],
        "manifest_files": [],
    }
    search = await invoke(
        "search_repositories",
        {"query": f"repo:{owner}/{name}", "perPage": 1, "minimal_output": False},
    )
    repo = _first_dict(_as_list(search)) or _as_dict(search)
    if repo:
        info["default_branch"] = repo.get("default_branch")
        info["description"] = repo.get("description")
        info["topics"] = repo.get("topics") or []
        lang = repo.get("language")
        if isinstance(repo.get("languages"), (list, dict)):
            info["languages"] = repo.get("languages")
        elif lang:
            info["languages"] = [{"name": lang, "bytes": 0}]
    commits = _as_list(
        await invoke("list_commits", {"owner": owner, "repo": name, "perPage": 1})
    )
    first = _first_dict(commits)
    if first:
        info["head_sha"] = first.get("sha")
    try:
        root = await invoke("get_file_contents", {"owner": owner, "repo": name, "path": ""})
        info["manifest_files"] = _manifest_names(root)
    except BaseException as exc:
        err = _as_global(exc, "")
        if err.error.code in ("github_auth", "github_rate_limit"):
            raise
    return info


async def _call(call_tool: CallTool, name: str, args: dict[str, Any]) -> Any:
    last: BaseException | None = None
    for attempt in range(3):
        try:
            result = call_tool(name, args)
            if inspect.isawaitable(result):
                result = await result
            return _jsonish(result)
        except GitHubCollectError:
            raise
        except BaseException as exc:
            last = exc
            status = _http_status(exc, str(exc))
            if status == 429 and attempt < 2:
                continue
            raise
    assert last is not None
    raise last


def _as_global(exc: BaseException, github_pat: str) -> GitHubCollectError:
    text = str(exc)
    if github_pat:
        text = text.replace(github_pat, "")
    status = _http_status(exc, text)
    if status == 401:
        return GitHubCollectError(
            JobError(code="github_auth", message="github authentication failed", retryable=False)
        )
    if status == 429:
        return GitHubCollectError(
            JobError(code="github_rate_limit", message="github rate limited", retryable=True)
        )
    return GitHubCollectError(
        JobError(code="mcp_unavailable", message="github mcp unavailable", retryable=True)
    )


def _http_status(exc: BaseException, text: str) -> int | None:
    cur: BaseException | None = exc
    for _ in range(4):
        if cur is None:
            break
        for attr in ("status_code", "status"):
            val = getattr(cur, attr, None)
            if isinstance(val, int) and val > 0:
                return val
        resp = getattr(cur, "response", None)
        if resp is not None:
            val = getattr(resp, "status_code", None)
            if isinstance(val, int) and val > 0:
                return val
        nxt = cur.__cause__ or cur.__context__
        cur = nxt if isinstance(nxt, BaseException) else None
    match = _STATUS_RE.search(text)
    if match:
        return int(match.group(1))
    low = text.lower()
    if "unauthorized" in low or "bad credentials" in low:
        return 401
    if "rate limit" in low or "too many requests" in low:
        return 429
    return None


def _jsonish(raw: Any) -> Any:
    if raw is None or isinstance(raw, (int, float, bool)):
        return raw
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list):
        if raw and all(isinstance(x, dict) and "text" in x for x in raw):
            parsed_items: list[Any] = []
            all_json = True
            for block in raw:
                text = str(block.get("text") or "").strip()
                if not text:
                    continue
                try:
                    parsed_items.append(json.loads(text))
                except json.JSONDecodeError:
                    all_json = False
                    break
            if all_json and parsed_items:
                if len(parsed_items) == 1:
                    return parsed_items[0]
                return parsed_items
            joined = "".join(str(x.get("text") or "") for x in raw)
            return _jsonish(joined)
        return raw
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    if not isinstance(raw, str):
        return raw
    s = raw.strip()
    if not s:
        return None
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return s


def _as_dict(raw: Any) -> dict[str, Any]:
    raw = _jsonish(raw)
    return raw if isinstance(raw, dict) else {}


def _as_list(raw: Any) -> list[Any]:
    raw = _jsonish(raw)
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for key in (
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
        ):
            val = raw.get(key)
            if isinstance(val, list):
                return val
        if raw.get("sha") or raw.get("number") or raw.get("full_name"):
            return [raw]
    return []


def _first_dict(rows: list[Any]) -> dict[str, Any] | None:
    for item in rows:
        if isinstance(item, dict):
            return item
    return None


def _login(raw: Any) -> str | None:
    if isinstance(raw, str) and raw:
        return raw
    data = _as_dict(raw)
    nested = data.get("data") if isinstance(data.get("data"), dict) else {}
    login = data.get("login") or data.get("username") or nested.get("login")
    return str(login) if login else None


def _ref(item: Any) -> RepoRef | None:
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
    if owner and name:
        return RepoRef(owner=str(owner), name=str(name))
    return None


def _refs(raw: Any) -> list[RepoRef]:
    out: list[RepoRef] = []
    for item in _as_list(raw):
        ref = _ref(item)
        if ref is not None:
            out.append(ref)
    return out


def _str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        inner = value.get("login") or value.get("name") or value.get("email")
        return str(inner) if inner is not None else None
    text = str(value)
    return text if text else None


def _iso(value: datetime) -> str:
    dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _languages(raw: Any) -> list[LanguageShare]:
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


def _commits(raw: Any) -> list[CommitSummary]:
    out: list[CommitSummary] = []
    for item in _commits_raw(raw):
        sha = item.get("sha")
        if not sha:
            continue
        out.append(
            CommitSummary(
                sha=str(sha),
                message=str(item.get("message") or ""),
                author=_str(item.get("author")),
                committed_at=item.get("committed_at"),
            )
        )
    return out


def _commits_raw(raw: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in _as_list(raw):
        if not isinstance(item, dict):
            continue
        commit = item.get("commit") if isinstance(item.get("commit"), dict) else {}
        author = item.get("author")
        if author is None:
            author = commit.get("author")
        committed_at = (
            item.get("committed_at")
            or item.get("date")
            or commit.get("committed_at")
            or (commit.get("committer") or {}).get("date")
            or (commit.get("author") or {}).get("date")
        )
        rows.append(
            {
                "sha": item.get("sha"),
                "message": item.get("message") or commit.get("message") or "",
                "author": author,
                "committed_at": committed_at,
            }
        )
    return rows


def _issues(raw: Any) -> list[IssueSummary]:
    out: list[IssueSummary] = []
    for item in _issues_raw(raw):
        if item.get("number") is None:
            continue
        out.append(
            IssueSummary(
                number=int(item["number"]),
                title=str(item.get("title") or ""),
                state=str(item.get("state") or ""),
                updated_at=item.get("updated_at"),
            )
        )
    return out


def _issues_raw(raw: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in _as_list(raw):
        if not isinstance(item, dict) or item.get("pull_request"):
            continue
        number = item.get("number") or item.get("issue_number")
        rows.append(
            {
                "number": number,
                "title": item.get("title") or "",
                "state": item.get("state") or "",
                "updated_at": item.get("updated_at") or item.get("updatedAt"),
            }
        )
    return rows


def _prs(raw: Any) -> list[PrSummary]:
    out: list[PrSummary] = []
    for item in _prs_raw(raw):
        if item.get("number") is None:
            continue
        out.append(
            PrSummary(
                number=int(item["number"]),
                title=str(item.get("title") or ""),
                state=str(item.get("state") or ""),
                updated_at=item.get("updated_at"),
            )
        )
    return out


def _prs_raw(raw: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in _as_list(raw):
        if not isinstance(item, dict):
            continue
        number = item.get("number") or item.get("pullNumber") or item.get("pull_number")
        rows.append(
            {
                "number": number,
                "title": item.get("title") or "",
                "state": item.get("state") or "",
                "updated_at": item.get("updated_at") or item.get("updatedAt"),
            }
        )
    return rows


def _readme(raw: Any) -> ReadmeBlob | None:
    data = _as_dict(raw)
    if not data:
        return None
    sha = data.get("blob_sha") or data.get("sha") or ""
    path = data.get("path") or README_PATH
    return ReadmeBlob(path=str(path), blob_sha=str(sha), content=_file_content(data))


def _file_content(data: dict[str, Any]) -> str:
    content = data.get("content")
    if content is None:
        return ""
    if not isinstance(content, str):
        return "" if isinstance(content, list) else str(content)
    if data.get("encoding") == "base64":
        import base64

        try:
            return base64.b64decode(content).decode("utf-8", errors="replace")
        except (ValueError, TypeError):
            return content
    return content


def _manifest_names(raw: Any) -> list[str]:
    names: list[str] = []
    for item in _as_list(raw):
        if isinstance(item, str):
            name = item.rsplit("/", 1)[-1]
        elif isinstance(item, dict):
            name = str(item.get("name") or item.get("path") or "").rsplit("/", 1)[-1]
        else:
            continue
        if name in MANIFESTS:
            names.append(name)
    return names
