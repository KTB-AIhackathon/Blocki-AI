from __future__ import annotations

from typing import Any

from app import render
from app.contracts import (
    ArtifactProposal,
    Evidence,
    EvidenceRef,
    GitHubSnapshot,
    JobRequest,
    ProfileFields,
)
from app.pipelines import common
from app.pipelines.portfolio import briefs, sections, team

KIND = "portfolio"


async def build(
    job: JobRequest,
    snapshot: GitHubSnapshot,
    evidence: Evidence,
    *,
    llm: Any | None = None,
    deadline: float | None = None,
) -> ArtifactProposal:
    if job.document is None:
        return common.blocked(job, KIND, ["document"])

    fields = job.document.profile_fields
    version = job.document.template_version
    template_ref = render.template_ref(KIND, version)

    missing = common.required_missing(KIND, fields)
    if missing:
        return common.blocked(job, KIND, missing, template_ref)

    sheets = briefs.render_briefs(evidence)
    _selected, dossiers, intro, selection_reason = await team.run_team(
        evidence, llm, deadline=deadline, sheets=sheets
    )
    evidence.selection_reason = selection_reason or evidence.selection_reason
    view = team.view_of(evidence, _selected)
    view = view.model_copy(update={"selection_reason": evidence.selection_reason})
    summary_md, summary_refs = sections.summary(view, intro)
    skills_md, skills_refs = sections.skills(view)
    projects_md, projects_refs = sections.projects(view, dossiers)
    learning_md, learning_refs = sections.learning(view)
    selection_md, selection_refs = common.selection(view, view.projects)

    body = render.render(
        KIND,
        version,
        {
            "name": fields.name,
            "contact_md": fields.contact_md,
            "summary_md": summary_md,
            "skills_md": skills_md,
            "projects_md": projects_md,
            "learning_md": learning_md,
            "selection_md": selection_md,
            "experience_md": fields.experience_md,
            "education_md": fields.education_md,
        },
    )

    unresolved = _unresolved(fields, skills_md, projects_md)
    refs: list[EvidenceRef] = [
        *summary_refs,
        *skills_refs,
        *projects_refs,
        *learning_refs,
        *selection_refs,
    ]
    complete = snapshot.complete and evidence.complete and not unresolved
    proposal = ArtifactProposal(
        proposal_id="",
        job_id=job.job_id,
        status="proposed" if complete else "partial",
        kind=KIND,
        owner_name=fields.name,
        body_markdown=body,
        template_ref=template_ref,
        evidence_refs=refs,
        unresolved_fields=unresolved,
        warnings=list(evidence.warnings),
    )
    proposal._publish_briefs = sheets
    proposal._hub_tail = briefs.hub_tail(evidence.unmatched_til)
    return proposal


def _unresolved(fields: ProfileFields, skills_md: str, projects_md: str) -> list[str]:
    unresolved: list[str] = []
    if not skills_md:
        unresolved.append("skills_md")
    if not projects_md:
        unresolved.append("projects_md")
    if not fields.contact_md.strip():
        unresolved.append("contact_md")
    return unresolved
