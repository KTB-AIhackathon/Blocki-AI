"""Incremental activity memo. Follows the cursor, so it only reports new work."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.contracts import (
    ArtifactProposal,
    Evidence,
    GitHubSnapshot,
    JobRequest,
    as_utc,
)

KIND = "progress"
UNDATED = "날짜 미상"


async def build(
    job: JobRequest,
    snapshot: GitHubSnapshot,
    evidence: Evidence,
    *,
    llm: Any | None = None,
) -> ArtifactProposal:
    items = _items(snapshot)
    if not items:
        return ArtifactProposal(
            proposal_id="",
            job_id=job.job_id,
            status="no_change" if snapshot.complete else "partial",
            kind=KIND,
            body_markdown="",
            warnings=list(snapshot.warnings),
        )
    return ArtifactProposal(
        proposal_id="",
        job_id=job.job_id,
        status="proposed" if snapshot.complete else "partial",
        kind=KIND,
        body_markdown=_memo(items),
        warnings=list(snapshot.warnings),
    )


def _items(snapshot: GitHubSnapshot) -> list[tuple[datetime | None, str]]:
    items: list[tuple[datetime | None, str]] = []
    for repo in snapshot.repos:
        name = repo.full_name
        for commit in repo.commits:
            subject = commit.message.splitlines()[0].strip() if commit.message else ""
            items.append(
                (
                    as_utc(commit.committed_at),
                    f"- `{name}` 커밋 `{commit.sha[:7]}`: {subject}".rstrip(),
                )
            )
        for issue in repo.issues:
            items.append(
                (
                    as_utc(issue.updated_at),
                    f"- `{name}` 이슈 #{issue.number}: {issue.title} ({issue.state})",
                )
            )
        for pr in repo.pull_requests:
            items.append(
                (
                    as_utc(pr.updated_at),
                    f"- `{name}` PR #{pr.number}: {pr.title} ({pr.state})",
                )
            )
    return items


def _memo(items: list[tuple[datetime | None, str]]) -> str:
    ordered = sorted(
        items,
        key=lambda row: (row[0] is None, -(row[0].timestamp()) if row[0] else 0, row[1]),
    )
    groups: dict[str, list[str]] = {}
    order: list[str] = []
    for when, line in ordered:
        key = when.date().isoformat() if when else UNDATED
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(line)

    parts = ["# 진행 메모", ""]
    for key in order:
        parts.extend([f"## {key}", "", *groups[key], ""])
    return "\n".join(parts).rstrip() + "\n"
