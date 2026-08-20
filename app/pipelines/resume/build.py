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
)
from app.llm import guard
from app.pipelines import common
from app.pipelines.resume import sections

KIND = "resume"
INSTRUCTION = (
    "EVIDENCE만 보고 소개를 한국어로 쓴다. 만든 사실만 쓴다. "
    "성격이나 미션 문장은 쓰지 않는다. 분량은 근거가 닿는 만큼만. "
    "각 문장은 근거 id를 함께 반환한다."
)


class _Intro(BaseModel):
    sentences: list[guard.GroundedText] = Field(default_factory=list)


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

    intro = await _intro_lines(evidence, llm)
    summary_md, summary_refs = sections.summary(evidence, intro)
    skills_md, skills_refs = sections.skills(evidence)
    projects_md, projects_refs = sections.projects(evidence)
    supplied, blank = common.user_supplied(KIND, fields)

    body = render.render(
        KIND,
        version,
        {
            "name": fields.name,
            "contact_md": fields.contact_md,
            "summary_md": summary_md,
            "skills_md": skills_md,
            "projects_md": projects_md,
            **supplied,
        },
    )

    unresolved = [
        field
        for field, value in (
            ("skills_md", skills_md),
            ("projects_md", projects_md),
            ("contact_md", fields.contact_md.strip()),
        )
        if not value
    ]
    unresolved.extend(blank)
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


async def _intro_lines(evidence: Evidence, llm: Any | None) -> list[str]:
    if evidence.is_empty():
        return []
    result = await guard.complete(_Intro, instruction=INSTRUCTION, evidence=evidence, llm=llm)
    if result is None:
        return []
    return [item.text for item in guard.keep_grounded(result.sentences, evidence.ids())]
