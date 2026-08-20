from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app import render
from app.contracts import (
    ArtifactProposal,
    Evidence,
    EvidenceRef,
    GitHubSnapshot,
    JobRequest,
    ProfileFields,
)
from app.llm import guard
from app.pipelines import common
from app.pipelines.portfolio import sections

KIND = "portfolio"
MAX_FEATURED = 3
INSTRUCTION = (
    "EVIDENCE만 보고 소개를 intro에 한국어로 쓴다. "
    "만든 사실만 쓴다. 성격이나 미션 문장은 쓰지 않는다. "
    "분량은 근거가 닿는 만큼만 쓴다. 얇으면 줄이거나 생략한다. "
    "각 문장에 evidence_ids를 넣는다."
)


class _Draft(BaseModel):
    intro: list[guard.GroundedText] = Field(default_factory=list)


async def build(
    job: JobRequest,
    snapshot: GitHubSnapshot,
    evidence: Evidence,
    *,
    llm: Any | None = None,
) -> ArtifactProposal:
    if job.document is None:
        return common.blocked(job, KIND, ["document"])

    fields = job.document.profile_fields
    version = job.document.template_version
    template_ref = render.template_ref(KIND, version)

    missing = common.required_missing(KIND, fields)
    if missing:
        return common.blocked(job, KIND, missing, template_ref)

    view = _featured(evidence)
    intro = await _draft(view, llm)
    summary_md, summary_refs = sections.summary(view, intro)
    skills_md, skills_refs = sections.skills(view)
    projects_md, projects_refs = sections.projects(view)

    body = render.render(
        KIND,
        version,
        {
            "name": fields.name,
            "contact_md": fields.contact_md,
            "summary_md": summary_md,
            "skills_md": skills_md,
            "projects_md": projects_md,
            "experience_md": fields.experience_md,
            "education_md": fields.education_md,
        },
    )

    unresolved = _unresolved(fields, skills_md, projects_md)
    refs: list[EvidenceRef] = [*summary_refs, *skills_refs, *projects_refs]
    complete = snapshot.complete and evidence.complete and not unresolved
    return ArtifactProposal(
        proposal_id="",
        job_id=job.job_id,
        status="proposed" if complete else "partial",
        kind=KIND,
        body_markdown=body,
        template_ref=template_ref,
        evidence_refs=refs,
        unresolved_fields=unresolved,
        warnings=list(evidence.warnings),
    )


def _featured(evidence: Evidence) -> Evidence:
    chosen = evidence.projects[:MAX_FEATURED]
    if len(chosen) == len(evidence.projects):
        return evidence
    starts = [p.started_at for p in chosen if p.started_at]
    ends = [p.ended_at for p in chosen if p.ended_at]
    return evidence.model_copy(
        update={
            "projects": chosen,
            "my_commits": sum(p.my_commits for p in chosen),
            "period_start": min(starts) if starts else evidence.period_start,
            "period_end": max(ends) if ends else evidence.period_end,
        }
    )


async def _draft(evidence: Evidence, llm: Any | None) -> list[guard.GroundedText]:
    if evidence.is_empty():
        return []
    result = await guard.complete(
        _Draft,
        instruction=INSTRUCTION,
        evidence=evidence,
        extra={"evidence": _digest(evidence)},
        llm=llm,
    )
    if result is None:
        return []
    return guard.keep_grounded(result.intro, evidence.ids())


def _digest(evidence: Evidence) -> dict[str, Any]:
    featured = {project.repo for project in evidence.projects}
    return {
        "viewer": evidence.viewer.login,
        "skills": [
            {"id": skill.id, "name": skill.name, "category": skill.category}
            for skill in evidence.skills
            if any(repo in featured for repo in skill.repos)
        ],
        "projects": [
            {
                "id": project.id,
                "repo": project.repo,
                "description": project.description,
                "languages": [skill.name for skill in project.languages],
                "highlights": [
                    {
                        "id": item.id,
                        "subject": item.subject,
                        "change_type": item.change_type,
                    }
                    for item in project.highlights
                ],
            }
            for project in evidence.projects
        ],
    }


def _unresolved(fields: ProfileFields, skills_md: str, projects_md: str) -> list[str]:
    unresolved: list[str] = []
    if not skills_md:
        unresolved.append("skills_md")
    if not projects_md:
        unresolved.append("projects_md")
    if not fields.contact_md.strip():
        unresolved.append("contact_md")
    return unresolved
