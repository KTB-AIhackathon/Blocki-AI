"""Read-only GitHub collect over the REST API.

Spring hands us an OAuth App token or a PAT. The hosted Copilot MCP server
does not accept the former, so collect talks to `api.github.com` instead.
Logical tool names stay the ones `collect_github` already calls.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote

import httpx

from app.collect.parse import README_PATH

API_URL = os.environ.get("GITHUB_API_URL", "https://api.github.com")
API_VERSION = os.environ.get("GITHUB_API_VERSION", "2022-11-28")


class GithubRestError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status


class RestCollector:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def __call__(self, name: str, args: dict[str, Any]) -> Any:
        args = args or {}
        if name == "get_me":
            user = await self._get("/user")
            return {"login": user.get("login"), "email": user.get("email")}
        if name == "list_repos":
            return await self._list_repos(int(args.get("limit") or 6))
        if name == "get_repo_meta":
            return await self._repo_meta(str(args["owner"]), str(args["name"]))
        if name == "list_commits":
            return await self._list_commits(args)
        if name == "list_issues":
            return await self._list_issues(args)
        if name == "list_pull_requests":
            return await self._list_pulls(args)
        if name == "get_file":
            return await self._get_file(args)
        raise RuntimeError(f"unknown logical tool: {name}")

    async def _list_repos(self, limit: int) -> list[dict[str, Any]]:
        rows = await self._get(
            "/user/repos",
            params={
                "sort": "updated",
                "affiliation": "owner,collaborator",
                "per_page": min(max(limit, 1), 100),
            },
        )
        return rows if isinstance(rows, list) else []

    async def _repo_meta(self, owner: str, name: str) -> dict[str, Any]:
        repo = await self._get(f"/repos/{_path(owner)}/{_path(name)}")
        languages = await self._get_optional(
            f"/repos/{_path(owner)}/{_path(name)}/languages", {}
        )
        commits = await self._get_optional(
            f"/repos/{_path(owner)}/{_path(name)}/commits",
            [],
            params={"per_page": 1},
        )
        root = await self._get_optional(
            f"/repos/{_path(owner)}/{_path(name)}/contents/",
            [],
        )
        head = commits[0] if isinstance(commits, list) and commits else {}
        return {
            "default_branch": repo.get("default_branch"),
            "head_sha": head.get("sha") if isinstance(head, dict) else None,
            "description": repo.get("description"),
            "html_url": repo.get("html_url"),
            "topics": repo.get("topics") or [],
            "languages": languages if isinstance(languages, dict) else {},
            "manifest_files": root if isinstance(root, list) else [],
            "fork": bool(repo.get("fork")),
            "archived": bool(repo.get("archived")),
            "stars": repo.get("stargazers_count") or repo.get("stars") or 0,
            "pushed_at": repo.get("pushed_at"),
        }

    async def _list_commits(self, args: dict[str, Any]) -> Any:
        params: dict[str, Any] = {
            "per_page": min(max(int(args.get("limit") or 30), 1), 100),
        }
        if args.get("since"):
            params["since"] = args["since"]
        if args.get("author"):
            params["author"] = args["author"]
        return await self._get_optional(
            f"/repos/{_path(args['owner'])}/{_path(args['name'])}/commits",
            [],
            params=params,
        )

    async def _list_issues(self, args: dict[str, Any]) -> Any:
        params: dict[str, Any] = {
            "state": "all",
            "per_page": min(max(int(args.get("limit") or 20), 1), 100),
        }
        if args.get("since"):
            params["since"] = args["since"]
        return await self._get(
            f"/repos/{_path(args['owner'])}/{_path(args['name'])}/issues",
            params=params,
        )

    async def _list_pulls(self, args: dict[str, Any]) -> Any:
        return await self._get(
            f"/repos/{_path(args['owner'])}/{_path(args['name'])}/pulls",
            params={
                "state": "all",
                "sort": "updated",
                "direction": "desc",
                "per_page": min(max(int(args.get("limit") or 20), 1), 100),
            },
        )

    async def _get_file(self, args: dict[str, Any]) -> dict[str, Any] | None:
        path = args.get("path") or README_PATH
        try:
            data = await self._get(
                f"/repos/{_path(args['owner'])}/{_path(args['name'])}/contents/{_content_path(path)}"
            )
        except GithubRestError as exc:
            if exc.status_code == 404:
                return None
            raise
        if not isinstance(data, dict):
            return None
        return {
            "path": data.get("path") or path,
            "blob_sha": data.get("sha") or "",
            "content": data.get("content") or "",
            "encoding": data.get("encoding") or "",
        }

    async def _get_optional(self, path: str, default: Any, **kwargs: Any) -> Any:
        try:
            return await self._get(path, **kwargs)
        except GithubRestError as exc:
            if exc.status_code in (404, 409):
                return default
            raise

    async def _get(self, path: str, **kwargs: Any) -> Any:
        response = await self._client.get(path, **kwargs)
        if response.status_code >= 400:
            detail = (response.text or "")[:180]
            raise GithubRestError(
                response.status_code,
                f"github api {response.status_code} {path}: {detail}",
            )
        payload = response.json()
        return payload


async def open_rest_session(
    github_pat: str, *, client: httpx.AsyncClient | None = None
) -> RestCollector:
    live = client or httpx.AsyncClient(
        base_url=API_URL.rstrip("/"),
        headers={
            "Authorization": f"Bearer {github_pat}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
        },
        timeout=30.0,
    )
    return RestCollector(live)


def _path(value: str) -> str:
    return quote(str(value), safe="")


def _content_path(path: str) -> str:
    return "/".join(quote(part, safe="") for part in str(path).split("/") if part)
