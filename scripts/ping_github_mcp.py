"""Ping GitHub MCP: get_me plus one repo. Usage: GITHUB_PAT=... python scripts/ping_github_mcp.py"""

from __future__ import annotations

import asyncio
import os
import sys

from app.collect.github import collect_github
from app.contracts import CollectRequest, GitHubCollectError


async def main() -> int:
    pat = os.environ.get("GITHUB_PAT") or os.environ.get("GITHUB_TOKEN") or ""
    if not pat.strip():
        print("GITHUB_PAT required", file=sys.stderr)
        return 1
    try:
        snap = await collect_github(
            CollectRequest(job_id="ping", needs=["activity"]),
            pat,
        )
    except GitHubCollectError as exc:
        print(
            f"error code={exc.error.code} retryable={exc.error.retryable}",
            file=sys.stderr,
        )
        return 1
    first = f"{snap.repos[0].owner}/{snap.repos[0].name}" if snap.repos else "-"
    print(
        f"login={snap.viewer_login} repos={len(snap.repos)} first={first} "
        f"complete={snap.complete}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
