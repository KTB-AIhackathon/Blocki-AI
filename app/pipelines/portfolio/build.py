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
INSTRUCTION = (
    "아래 개발자의 GitHub 활동 근거만 보고 포트폴리오 자기소개를 2~3문장으로 써라. "
    "각 문장은 근거 id를 함께 반환한다. 근거가 부족하면 문장 수를 줄인다."
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
    stats_md, stats_refs = sections.stats(evidence)
    skills_md, skills_refs = sections.skills(evidence)
    projects_md, projects_refs = sections.projects(evidence)

    body = render.render(
        KIND,
        version,
        {
            "name": fields.name,
            "contact_md": fields.contact_md,
            "summary_md": summary_md,
            "stats_md": stats_md,
            "skills_md": skills_md,
            "projects_md": projects_md,
            "experience_md": fields.experience_md,
            "education_md": fields.education_md,
        },
    )

    unresolved = _unresolved(fields, summary_md, skills_md, projects_md)
    refs: list[EvidenceRef] = [*summary_refs, *stats_refs, *skills_refs, *projects_refs]
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
    kept = guard.keep_grounded(result.sentences, evidence.ids())
    return [item.text for item in kept]


def _unresolved(
    fields: ProfileFields, summary_md: str, skills_md: str, projects_md: str
) -> list[str]:
    unresolved: list[str] = []
    if not summary_md:
        unresolved.append("summary_md")
    if not skills_md:
        unresolved.append("skills_md")
    if not projects_md:
        unresolved.append("projects_md")
    if not fields.contact_md.strip():
        unresolved.append("contact_md")
    return unresolved
