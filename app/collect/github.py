"""Deterministic GitHub snapshot. Fixed tool calls, no LLM in the loop."""

from __future__ import annotations

import asyncio
import inspect
from datetime import datetime
from typing import Any

from app.collect import parse
from app.collect.mcp import CallTool, open_read_session
from app.contracts import (
    CollectPolicy,
    CollectRequest,
    CommitSummary,
    GitHubCollectError,
    GitHubSnapshot,
    IssueSummary,
    JobError,
    PrSummary,
    RepoActivity,
    RepoCursor,
    RepoRef,
    ViewerIdentity,
    snapshot_digest_of,
    utcnow,
)

# Spring shows this to the user, so it names the fix rather than the symptom.
SCOPE_HINT = (
    "GitHub 권한이 부족합니다. GitHub 연결을 다시 하고 "
    "read:user(내 계정 확인)와 repo(비공개 저장소 읽기·README PR) 권한을 허용해주세요."
)


async def collect_github(
    req: CollectRequest, github_pat: str, *, call_tool: CallTool | None = None
) -> GitHubSnapshot:
    if call_tool is None:
        try:
            call_tool = await open_read_session(github_pat)
        except GitHubCollectError:
            raise
        except BaseException as exc:
            raise _fatal(exc, github_pat) from None

    policy = req.policy
    collected_at = utcnow()
    warnings: list[str] = []
    complete = True

    me = await _guarded(call_tool, "get_me", {}, github_pat)
    viewer = _viewer_of(me)

    refs = list(req.repos)
    if not refs:
        listed = await _guarded(call_tool, "list_repos", {"limit": policy.max_repos}, github_pat)
        refs = parse.repo_refs(listed)[: policy.max_repos]

    cursors = {(c.owner, c.name): c for c in (req.cursor or [])} if policy.use_cursor else {}
    since = None if policy.full_history else req.since
    targets = refs[: policy.max_repos]

    # Repositories do not depend on each other, and each one is six sequential round trips to
    # a remote server. Gathered results are still read back in `targets` order, because the
    # snapshot digest and the project list must not depend on which repository answered first.
    limit = asyncio.Semaphore(max(1, policy.max_concurrency))

    async def collect_one(ref: RepoRef):
        async with limit:
            return await _collect_repo(
                call_tool,
                ref,
                policy=policy,
                viewer=viewer,
                since=since,
                cursor=cursors.get((ref.owner, ref.name)),
                collected_at=collected_at,
                readme_path=req.readme_path or parse.README_PATH,
            )

    outcomes = await asyncio.gather(
        *(collect_one(ref) for ref in targets), return_exceptions=True
    )

    repos: list[RepoActivity] = []
    next_cursor: list[RepoCursor] = []
    for ref, outcome in zip(targets, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            if isinstance(outcome, GitHubCollectError):
                raise outcome
            error = _fatal(outcome, github_pat)
            if error.error.code in ("github_auth", "github_rate_limit"):
                raise error from None
            # One unreadable repository is not worth failing the job over, but the reason has
            # to travel with the snapshot or the thin document looks like a bug.
            reason = "권한 부족" if error.error.code == "github_scope" else "읽기 실패"
            warnings.append(f"{ref.owner}/{ref.name} 건너뜀 ({reason})")
            complete = False
            continue
        activity, cursor = outcome
        repos.append(activity)
        if cursor is not None:
            next_cursor.append(cursor)

    return GitHubSnapshot(
        collected_at=collected_at,
        complete=complete,
        snapshot_digest=snapshot_digest_of(repos, viewer.login),
        viewer_login=viewer.login,
        repos=repos,
        next_cursor=next_cursor,
        warnings=warnings,
    )


async def _collect_repo(
    call_tool: CallTool,
    ref: RepoRef,
    *,
    policy: CollectPolicy,
    viewer: ViewerIdentity,
    since: datetime | None,
    cursor: RepoCursor | None,
    collected_at: datetime,
    readme_path: str,
) -> tuple[RepoActivity, RepoCursor | None]:
    meta = parse.as_dict(
        await _call(call_tool, "get_repo_meta", {"owner": ref.owner, "name": ref.name})
    )
    head_sha = parse.text_of(meta.get("head_sha") or meta.get("sha"))

    commits: list[CommitSummary] = []
    issues: list[IssueSummary] = []
    prs: list[PrSummary] = []
    if "activity" in policy.needs:
        commits, issues, prs = await _collect_activity(
            call_tool,
            ref,
            policy=policy,
            viewer=viewer,
            since=cursor.last_success_at if cursor is not None else since,
            skip_commits=cursor is not None and bool(head_sha) and cursor.head_sha == head_sha,
        )

    readme = None
    if "readme" in policy.needs:
        readme = await _collect_readme(call_tool, ref, readme_path)

    activity = RepoActivity(
        owner=ref.owner,
        name=ref.name,
        default_branch=parse.text_of(meta.get("default_branch")),
        head_sha=head_sha,
        description=parse.text_of(meta.get("description")),
        html_url=parse.text_of(meta.get("html_url")),
        topics=[str(t) for t in (meta.get("topics") or []) if t is not None],
        languages=parse.languages(meta.get("languages")),
        manifest_files=[
            name
            for item in (meta.get("manifest_files") or [])
            if (name := parse.entry_name(item))
        ],
        fork=bool(meta.get("fork")),
        archived=bool(meta.get("archived")),
        stars=parse.as_int(meta.get("stars")),
        pushed_at=parse.when(meta.get("pushed_at")),
        commits=commits,
        issues=issues,
        pull_requests=prs,
        readme=readme,
    )
    nxt = (
        RepoCursor(
            owner=ref.owner,
            name=ref.name,
            head_sha=head_sha,
            last_success_at=collected_at,
        )
        if head_sha
        else None
    )
    return activity, nxt


async def _collect_activity(
    call_tool: CallTool,
    ref: RepoRef,
    *,
    policy: CollectPolicy,
    viewer: ViewerIdentity,
    since: datetime | None,
    skip_commits: bool,
) -> tuple[list[CommitSummary], list[IssueSummary], list[PrSummary]]:
    args: dict[str, Any] = {"owner": ref.owner, "name": ref.name}
    if since is not None:
        args["since"] = parse.iso(since)

    commits: list[CommitSummary] = []
    if not skip_commits:
        commit_args = {**args, "limit": policy.max_commits}
        if policy.author_only and viewer.login:
            commit_args["author"] = viewer.login
        rows = parse.commits(await _call(call_tool, "list_commits", commit_args))
        for row in rows:
            row.mine = viewer.owns(row.author, row.author_email)
        # A server-side author filter is the cheap path, but stubs and older
        # servers ignore it, so drop foreign commits here too rather than
        # crediting a teammate's work.
        if policy.author_only and viewer.login and any(r.mine for r in rows):
            rows = [r for r in rows if r.mine]
        commits = rows[: policy.max_commits]

    issues = parse.issues(
        await _call(call_tool, "list_issues", {**args, "limit": policy.max_issues})
    )[: policy.max_issues]
    prs = parse.prs(
        await _call(call_tool, "list_pull_requests", {**args, "limit": policy.max_prs})
    )[: policy.max_prs]
    return commits, issues, prs


async def _collect_readme(call_tool: CallTool, ref: RepoRef, path: str):
    try:
        blob = await _call(
            call_tool, "get_file", {"owner": ref.owner, "name": ref.name, "path": path}
        )
    except GitHubCollectError:
        raise
    except BaseException as exc:
        if parse.http_status(exc, str(exc)) == 404:
            return None
        raise
    return parse.readme_blob(blob)


def _viewer_of(raw: Any) -> ViewerIdentity:
    data = parse.as_dict(raw)
    login = parse.login_of(raw)
    aliases = {
        value.strip().casefold()
        for value in (login, data.get("email"), data.get("name"))
        if isinstance(value, str) and value.strip()
    }
    return ViewerIdentity(login=login, aliases=sorted(aliases))


AUTH_RETRY_SECONDS = 2.0


async def _call(call_tool: CallTool, name: str, args: dict[str, Any]) -> Any:
    for attempt in range(3):
        try:
            result = call_tool(name, args)
            if inspect.isawaitable(result):
                result = await result
            return parse.jsonish(result)
        except GitHubCollectError:
            raise
        except BaseException as exc:
            status = parse.http_status(exc, str(exc))
            if status == 429 and attempt < 2:
                continue
            if status == 401 and attempt < 1:
                await asyncio.sleep(AUTH_RETRY_SECONDS)
                continue
            raise
    raise RuntimeError(f"collect exhausted retries: {name}")


async def _guarded(call_tool: CallTool, name: str, args: dict[str, Any], pat: str) -> Any:
    try:
        return await _call(call_tool, name, args)
    except GitHubCollectError:
        raise
    except BaseException as exc:
        raise _fatal(exc, pat) from None


def _fatal(exc: BaseException, github_pat: str) -> GitHubCollectError:
    text = str(exc).replace(github_pat, "") if github_pat else str(exc)
    status = parse.http_status(exc, text)
    if status == 401:
        return GitHubCollectError(
            JobError(code="github_auth", message="github authentication failed", retryable=False)
        )
    if status == 429:
        return GitHubCollectError(
            JobError(code="github_rate_limit", message="github rate limited", retryable=True)
        )
    # The token works, it just is not allowed to read this. Retrying changes nothing, and an
    # empty document with no explanation is the worst possible answer, so name the fix.
    if status == 403:
        return GitHubCollectError(JobError(code="github_scope", message=SCOPE_HINT, retryable=False))
    return GitHubCollectError(
        JobError(code="mcp_unavailable", message="github mcp unavailable", retryable=True)
    )
