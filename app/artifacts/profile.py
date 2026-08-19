from __future__ import annotations

from hashlib import sha256

from app.contracts import (
    ArtifactProposal,
    DocumentKind,
    EvidenceRef,
    GitHubSnapshot,
    JobError,
    JobRequest,
    ProfileFields,
    TemplateRef,
)
from app.templates_render import load_template, render_template, template_path


def build(snapshot: GitHubSnapshot, job: JobRequest, llm=None) -> ArtifactProposal:
    if job.document is None:
        return ArtifactProposal(
            proposal_id="",
            job_id=job.job_id,
            status="blocked",
            kind="portfolio",
            error=JobError(code="blocked", message="document spec required", retryable=False),
            unresolved_fields=["document"],
        )

    kind = job.document.kind
    version = job.document.template_version
    fields = job.document.profile_fields
    ref = _template_ref(kind, version)
    missing = _required_missing(kind, fields)
    if missing:
        return ArtifactProposal(
            proposal_id="",
            job_id=job.job_id,
            status="blocked",
            kind=kind,
            template_ref=ref,
            unresolved_fields=missing,
            error=JobError(
                code="blocked",
                message="필수 프로필 필드 누락: " + ", ".join(missing),
                retryable=False,
            ),
        )

    summary_md, summary_refs = _summary_md(snapshot)
    skills_md, skills_refs = _skills_md(snapshot)
    projects_md, projects_refs = _projects_md(snapshot)

    unresolved: list[str] = []
    if not summary_md:
        unresolved.append("summary_md")
    if not skills_md:
        unresolved.append("skills_md")
    if not projects_md:
        unresolved.append("projects_md")

    body = render_template(
        load_template(kind, version),
        {
            "name": fields.name,
            "contact_md": fields.contact_md,
            "experience_md": fields.experience_md,
            "education_md": fields.education_md,
            "summary_md": summary_md,
            "skills_md": skills_md,
            "projects_md": projects_md,
        },
    )
    return ArtifactProposal(
        proposal_id="",
        job_id=job.job_id,
        status="proposed" if snapshot.complete else "partial",
        kind=kind,
        body_markdown=body,
        template_ref=ref,
        evidence_refs=[*summary_refs, *skills_refs, *projects_refs],
        unresolved_fields=unresolved,
    )


def _required_missing(kind: DocumentKind, fields: ProfileFields) -> list[str]:
    missing: list[str] = []
    if not fields.name.strip():
        missing.append("name")
    if kind == "resume":
        if not fields.experience_md.strip():
            missing.append("experience_md")
        if not fields.education_md.strip():
            missing.append("education_md")
    return missing


def _template_ref(kind: DocumentKind, version: str) -> TemplateRef:
    digest = sha256(template_path(kind, version).read_bytes()).hexdigest()
    return TemplateRef(kind=kind, version=version, sha256=digest)


def _repo_id(owner: str, name: str) -> str:
    return f"{owner}/{name}"


def _first_line(message: str) -> str:
    return message.splitlines()[0].strip() if message else ""


def _skills_md(snapshot: GitHubSnapshot) -> tuple[str, list[EvidenceRef]]:
    seen: set[str] = set()
    bullets: list[str] = []
    refs: list[EvidenceRef] = []
    for repo in snapshot.repos:
        repo_key = _repo_id(repo.owner, repo.name)
        for lang in repo.languages:
            _add_skill(lang.name, "language", repo_key, seen, bullets, refs)
        for topic in repo.topics:
            _add_skill(topic, "topic", repo_key, seen, bullets, refs)
        for manifest in repo.manifest_files:
            _add_skill(manifest, "manifest", repo_key, seen, bullets, refs)
    if not bullets:
        return "", []
    return "\n".join(bullets), refs


def _add_skill(
    raw: str,
    source_type: str,
    repo_key: str,
    seen: set[str],
    bullets: list[str],
    refs: list[EvidenceRef],
) -> None:
    name = raw.strip()
    if not name:
        return
    key = f"{source_type}:{name.casefold()}"
    if key not in seen:
        seen.add(key)
        bullets.append(f"- {name}")
        refs.append(
            EvidenceRef(
                field="skills_md",
                repo=repo_key,
                source_type=source_type,
                source_id=name,
            )
        )


def _summary_md(snapshot: GitHubSnapshot) -> tuple[str, list[EvidenceRef]]:
    if not snapshot.repos:
        return "", []
    lines: list[str] = []
    if snapshot.viewer_login:
        lines.append(f"{snapshot.viewer_login}의 GitHub 저장소 활동 요약입니다.")
    else:
        lines.append("GitHub 저장소 활동 요약입니다.")
    refs: list[EvidenceRef] = []
    for repo in snapshot.repos:
        repo_key = _repo_id(repo.owner, repo.name)
        desc = (repo.description or "").strip()
        counts = (
            f"커밋 {len(repo.commits)}개, 이슈 {len(repo.issues)}개, "
            f"PR {len(repo.pull_requests)}개"
        )
        if desc:
            lines.append(f"- `{repo_key}`: {desc} ({counts})")
        else:
            lines.append(f"- `{repo_key}`: {counts}")
        refs.append(
            EvidenceRef(
                field="summary_md",
                repo=repo_key,
                source_type="repo",
                source_id=repo.head_sha or repo.name,
            )
        )
    return "\n".join(lines), refs


def _projects_md(snapshot: GitHubSnapshot) -> tuple[str, list[EvidenceRef]]:
    if not snapshot.repos:
        return "", []
    blocks: list[str] = []
    refs: list[EvidenceRef] = []
    for repo in snapshot.repos:
        repo_key = _repo_id(repo.owner, repo.name)
        parts = [f"### `{repo_key}`", ""]
        if (repo.description or "").strip():
            parts.append(repo.description.strip())
            parts.append("")
        refs.append(
            EvidenceRef(
                field="projects_md",
                repo=repo_key,
                source_type="repo",
                source_id=repo.head_sha or repo.name,
            )
        )
        for commit in repo.commits:
            msg = _first_line(commit.message)
            parts.append(f"- 커밋 `{commit.sha[:7]}`: {msg}".rstrip())
            refs.append(
                EvidenceRef(
                    field="projects_md",
                    repo=repo_key,
                    source_type="commit",
                    source_id=commit.sha,
                )
            )
        for issue in repo.issues:
            parts.append(f"- 이슈 #{issue.number}: {issue.title} ({issue.state})")
            refs.append(
                EvidenceRef(
                    field="projects_md",
                    repo=repo_key,
                    source_type="issue",
                    source_id=str(issue.number),
                )
            )
        for pr in repo.pull_requests:
            parts.append(f"- PR #{pr.number}: {pr.title} ({pr.state})")
            refs.append(
                EvidenceRef(
                    field="projects_md",
                    repo=repo_key,
                    source_type="pull_request",
                    source_id=str(pr.number),
                )
            )
        blocks.append("\n".join(parts).rstrip())
    return "\n\n".join(blocks), refs
