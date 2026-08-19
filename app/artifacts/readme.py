from __future__ import annotations

import re

from app.contracts import (
    ArtifactProposal,
    GitHubSnapshot,
    JobError,
    JobRequest,
    ReadmePrAction,
    ReadmeTarget,
    RepoActivity,
)

_HEADING_RE = re.compile(r"^(#{1,6})([ \t]*)(.*)$")


def build(snapshot: GitHubSnapshot, job: JobRequest, llm=None) -> ArtifactProposal:
    target = job.readme
    if target is None:
        return ArtifactProposal(
            proposal_id="",
            job_id=job.job_id,
            status="blocked",
            kind="readme",
            error=JobError(code="validation", message="readme target required", retryable=False),
            unresolved_fields=["readme"],
        )

    repo = _find_repo(snapshot, target.owner, target.repo)
    current = repo.readme.content if repo and repo.readme else ""
    if current:
        replacement = _with_activity(_improve_headings(current), repo)
    else:
        replacement = _new_readme(repo, target)

    if replacement == current:
        return ArtifactProposal(
            proposal_id="",
            job_id=job.job_id,
            status="no_change",
            kind="readme",
            body_markdown=replacement,
            proposed_action=None,
        )

    action = ReadmePrAction(
        type="create_readme_pr",
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
        kind="readme",
        body_markdown=replacement,
        proposed_action=action,
    )


def _find_repo(snapshot: GitHubSnapshot, owner: str, name: str) -> RepoActivity | None:
    for repo in snapshot.repos:
        if repo.owner == owner and repo.name == name:
            return repo
    return None


def _activity_lines(repo: RepoActivity | None) -> list[str]:
    if repo is None:
        return []
    lines: list[str] = []
    for commit in repo.commits:
        msg = commit.message.splitlines()[0].strip() if commit.message else ""
        lines.append(f"- 커밋 `{commit.sha[:7]}`: {msg}".rstrip())
    for issue in repo.issues:
        lines.append(f"- 이슈 #{issue.number}: {issue.title} ({issue.state})")
    for pr in repo.pull_requests:
        lines.append(f"- PR #{pr.number}: {pr.title} ({pr.state})")
    return lines


def _with_activity(content: str, repo: RepoActivity | None) -> str:
    activity = _activity_lines(repo)
    if not activity:
        return content
    marker = "## 최근 활동"
    block = marker + "\n\n" + "\n".join(activity) + "\n"
    if marker in content:
        before, _, rest = content.partition(marker)
        nxt = rest.find("\n## ")
        after = rest[nxt:] if nxt != -1 else ""
        return (before.rstrip() + "\n\n" + block + after.lstrip("\n")).rstrip() + "\n"
    return content.rstrip() + "\n\n" + block


def _improve_headings(content: str) -> str:
    trailing_nl = content.endswith("\n")
    lines = content.splitlines()
    out: list[str] = []
    for line in lines:
        match = _HEADING_RE.match(line)
        if match:
            hashes, _, rest = match.groups()
            rest = rest.strip()
            out.append(f"{hashes} {rest}" if rest else hashes)
        else:
            out.append(line)
    text = "\n".join(out)
    if trailing_nl:
        text += "\n"
    return text


def _new_readme(repo: RepoActivity | None, target: ReadmeTarget) -> str:
    title = repo.name if repo else target.repo
    lines = [f"# {title}", ""]
    if repo and (repo.description or "").strip():
        lines.append(repo.description.strip())
        lines.append("")
    activity = _activity_lines(repo)
    if activity:
        lines.append("## 최근 활동")
        lines.append("")
        lines.extend(activity)
        lines.append("")
    text = "\n".join(lines)
    if not text.endswith("\n"):
        text += "\n"
    return text
