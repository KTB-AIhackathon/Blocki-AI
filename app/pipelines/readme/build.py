"""README improvement proposal. Produces a diff candidate, never a PR."""

from __future__ import annotations

import re
from typing import Any

from app.contracts import (
    ArtifactProposal,
    Evidence,
    GitHubSnapshot,
    JobError,
    JobRequest,
    ReadmePrAction,
    ReadmeTarget,
    RepoActivity,
)

KIND = "readme"
ACTIVITY_HEADING = "## 최근 활동"

_HEADING_RE = re.compile(r"^(#{1,6})([ \t]*)(.*)$")


async def build(
    job: JobRequest,
    snapshot: GitHubSnapshot,
    evidence: Evidence,
    *,
    llm: Any | None = None,
) -> ArtifactProposal:
    target = job.readme
    if target is None:
        return ArtifactProposal(
            proposal_id="",
            job_id=job.job_id,
            status="blocked",
            kind=KIND,
            unresolved_fields=["readme"],
            error=JobError(code="validation", message="readme target required", retryable=False),
        )

    repo = _find(snapshot, target.owner, target.repo)
    current = repo.readme.content if repo and repo.readme else ""
    replacement = (
        _with_activity(_normalize_headings(current), repo) if current else _new(repo, target)
    )

    if replacement == current:
        return ArtifactProposal(
            proposal_id="",
            job_id=job.job_id,
            status="no_change",
            kind=KIND,
            body_markdown=replacement,
        )

    action = ReadmePrAction(
        owner=target.owner,
        repo=target.repo,
        path=target.path,
        base_branch=repo.default_branch if repo and repo.default_branch else "main",
        expected_base_sha=repo.head_sha if repo and repo.head_sha else "",
        expected_blob_sha=repo.readme.blob_sha if repo and repo.readme else "",
        replacement_markdown=replacement,
        pr_title=f"docs: {target.path} 업데이트",
        pr_body="Blocki-AI가 생성한 README 제안입니다.",
    )
    return ArtifactProposal(
        proposal_id="",
        job_id=job.job_id,
        status="proposed" if snapshot.complete else "partial",
        kind=KIND,
        body_markdown=replacement,
        proposed_action=action,
        warnings=list(snapshot.warnings),
    )


def _find(snapshot: GitHubSnapshot, owner: str, name: str) -> RepoActivity | None:
    for repo in snapshot.repos:
        if repo.owner == owner and repo.name == name:
            return repo
    return None


def _activity_lines(repo: RepoActivity | None) -> list[str]:
    if repo is None:
        return []
    lines = [
        f"- 커밋 `{c.sha[:7]}`: {c.message.splitlines()[0].strip() if c.message else ''}".rstrip()
        for c in repo.commits
    ]
    lines += [f"- 이슈 #{i.number}: {i.title} ({i.state})" for i in repo.issues]
    lines += [f"- PR #{p.number}: {p.title} ({p.state})" for p in repo.pull_requests]
    return lines


def _with_activity(content: str, repo: RepoActivity | None) -> str:
    activity = _activity_lines(repo)
    if not activity:
        return content
    block = ACTIVITY_HEADING + "\n\n" + "\n".join(activity) + "\n"
    if ACTIVITY_HEADING in content:
        before, _, rest = content.partition(ACTIVITY_HEADING)
        nxt = rest.find("\n## ")
        after = rest[nxt:] if nxt != -1 else ""
        return (before.rstrip() + "\n\n" + block + after.lstrip("\n")).rstrip() + "\n"
    return content.rstrip() + "\n\n" + block


def _normalize_headings(content: str) -> str:
    trailing = content.endswith("\n")
    out: list[str] = []
    for line in content.splitlines():
        match = _HEADING_RE.match(line)
        if match:
            hashes, _, rest = match.groups()
            rest = rest.strip()
            out.append(f"{hashes} {rest}" if rest else hashes)
        else:
            out.append(line)
    text = "\n".join(out)
    return text + "\n" if trailing else text


def _new(repo: RepoActivity | None, target: ReadmeTarget) -> str:
    lines = [f"# {repo.name if repo else target.repo}", ""]
    if repo and (repo.description or "").strip():
        lines.extend([repo.description.strip(), ""])
    activity = _activity_lines(repo)
    if activity:
        lines.extend([ACTIVITY_HEADING, "", *activity, ""])
    text = "\n".join(lines)
    return text if text.endswith("\n") else text + "\n"
