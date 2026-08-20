"""GitHub collect session.

Logical tool names (`get_me`, `list_repos`, …) stay stable so `collect_github`
and tests do not care about transport. The live session is GitHub REST;
hosted Copilot MCP is not used for collect.
"""

from __future__ import annotations

import os
from typing import Any, Callable

from app.collect import parse

CallTool = Callable[[str, dict[str, Any]], Any]

TOOLSETS = "context,repos,issues,pull_requests"


def mcp_url() -> str:
    return os.environ.get("GITHUB_MCP_URL", "https://api.githubcopilot.com/mcp/")


async def open_read_session(github_pat: str) -> CallTool:
    from app.collect.github_rest import open_rest_session

    return await open_rest_session(github_pat)


async def _logical(invoke, viewer: dict[str, str | None], name: str, args: dict[str, Any]) -> Any:
    if name == "get_me":
        data = parse.as_dict(await invoke("get_me", {}))
        viewer["login"] = parse.login_of(data)
        return {"login": viewer["login"], "email": data.get("email")}

    if name == "list_repos":
        login = viewer["login"] or parse.login_of(await invoke("get_me", {}))
        viewer["login"] = login
        query = f"user:{login} fork:false" if login else "is:public"
        data = await invoke(
            "search_repositories",
            {
                "query": query,
                "perPage": int(args.get("limit") or 5),
                "sort": "updated",
                "order": "desc",
                "minimal_output": False,
            },
        )
        return [r.model_dump() for r in parse.repo_refs(data)]

    if name == "get_repo_meta":
        return await _repo_meta(invoke, args["owner"], args["name"])

    if name == "list_commits":
        payload: dict[str, Any] = {
            "owner": args["owner"],
            "repo": args["name"],
            "perPage": int(args.get("limit") or 30),
        }
        if args.get("since"):
            payload["since"] = args["since"]
        if args.get("author"):
            payload["author"] = args["author"]
        return parse.commits_raw(await invoke("list_commits", payload))

    if name == "list_issues":
        payload = {
            "owner": args["owner"],
            "repo": args["name"],
            "perPage": int(args.get("limit") or 20),
            "state": "all",
        }
        if args.get("since"):
            payload["since"] = args["since"]
        return parse.issues_raw(await invoke("list_issues", payload))

    if name == "list_pull_requests":
        payload = {
            "owner": args["owner"],
            "repo": args["name"],
            "perPage": int(args.get("limit") or 20),
            "state": "all",
            "sort": "updated",
            "direction": "desc",
        }
        rows = parse.prs_raw(await invoke("list_pull_requests", payload))
        since = args.get("since")
        if since:
            rows = [r for r in rows if str(r.get("updated_at") or "") >= str(since)]
        return rows

    if name == "get_file":
        try:
            data = parse.as_dict(
                await invoke(
                    "get_file_contents",
                    {
                        "owner": args["owner"],
                        "repo": args["name"],
                        "path": args.get("path") or parse.README_PATH,
                    },
                )
            )
        except BaseException as exc:
            if parse.http_status(exc, str(exc)) == 404:
                return None
            raise
        return {
            "path": data.get("path") or args.get("path") or parse.README_PATH,
            "blob_sha": data.get("sha") or data.get("blob_sha") or "",
            "content": parse.file_content(data),
        }

    raise RuntimeError(f"unknown logical tool: {name}")


async def _repo_meta(invoke, owner: str, name: str) -> dict[str, Any]:
    info: dict[str, Any] = {
        "default_branch": None,
        "head_sha": None,
        "description": None,
        "html_url": f"https://github.com/{owner}/{name}",
        "topics": [],
        "languages": [],
        "manifest_files": [],
        "fork": False,
        "archived": False,
        "stars": 0,
        "pushed_at": None,
    }
    search = await invoke(
        "search_repositories",
        {"query": f"repo:{owner}/{name}", "perPage": 1, "minimal_output": False},
    )
    repo = parse.first_dict(parse.as_list(search)) or parse.as_dict(search)
    if repo:
        info["default_branch"] = repo.get("default_branch")
        info["description"] = repo.get("description")
        info["html_url"] = repo.get("html_url") or info["html_url"]
        info["topics"] = repo.get("topics") or []
        info["fork"] = bool(repo.get("fork"))
        info["archived"] = bool(repo.get("archived"))
        info["pushed_at"] = repo.get("pushed_at") or repo.get("pushedAt")
        try:
            info["stars"] = int(repo.get("stargazers_count") or repo.get("stars") or 0)
        except (TypeError, ValueError):
            info["stars"] = 0
        if isinstance(repo.get("languages"), (list, dict)):
            info["languages"] = repo["languages"]
        elif repo.get("language"):
            info["languages"] = [{"name": repo["language"], "bytes": 0}]

    head = parse.first_dict(
        parse.as_list(await invoke("list_commits", {"owner": owner, "repo": name, "perPage": 1}))
    )
    if head:
        info["head_sha"] = head.get("sha")

    try:
        root = await invoke("get_file_contents", {"owner": owner, "repo": name, "path": ""})
        info["manifest_files"] = parse.manifest_names(root)
    except BaseException as exc:
        # A missing tree listing only costs us manifest hints; auth and rate
        # limit failures must still stop the whole collect.
        if parse.http_status(exc, str(exc)) in (401, 429):
            raise
    return info
