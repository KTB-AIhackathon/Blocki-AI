from __future__ import annotations

from datetime import datetime, timezone

from app.contracts import ArtifactProposal, GitHubSnapshot, JobRequest


def build(snapshot: GitHubSnapshot, job: JobRequest, llm=None) -> ArtifactProposal:
    items = _activity_items(snapshot)
    if not items:
        status = "no_change" if snapshot.complete else "partial"
        body = ""
    else:
        status = "proposed" if snapshot.complete else "partial"
        body = _render_memo(items)
    return ArtifactProposal(
        proposal_id="",
        job_id=job.job_id,
        status=status,
        kind="progress",
        body_markdown=body,
        proposed_action=None,
    )


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _first_line(message: str) -> str:
    return message.splitlines()[0].strip() if message else ""


def _activity_items(snapshot: GitHubSnapshot) -> list[tuple[datetime | None, str]]:
    items: list[tuple[datetime | None, str]] = []
    for repo in snapshot.repos:
        repo_id = f"{repo.owner}/{repo.name}"
        for commit in repo.commits:
            short = commit.sha[:7]
            msg = _first_line(commit.message)
            line = f"- `{repo_id}` 커밋 `{short}`: {msg}".rstrip()
            items.append((_as_utc(commit.committed_at), line))
        for issue in repo.issues:
            line = f"- `{repo_id}` 이슈 #{issue.number}: {issue.title} ({issue.state})"
            items.append((_as_utc(issue.updated_at), line))
        for pr in repo.pull_requests:
            line = f"- `{repo_id}` PR #{pr.number}: {pr.title} ({pr.state})"
            items.append((_as_utc(pr.updated_at), line))
    return items


def _render_memo(items: list[tuple[datetime | None, str]]) -> str:
    keyed = [(dt, index, line) for index, (dt, line) in enumerate(items)]
    keyed.sort(key=lambda row: (row[0] is None, -(row[0].timestamp()) if row[0] else 0, row[1]))

    groups: dict[str, list[str]] = {}
    order: list[str] = []
    for dt, _, line in keyed:
        key = dt.date().isoformat() if dt else "날짜 미상"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(line)

    parts = ["# 진행 메모", ""]
    for key in order:
        parts.append(f"## {key}")
        parts.append("")
        parts.extend(groups[key])
        parts.append("")
    text = "\n".join(parts).rstrip()
    return text + "\n"
