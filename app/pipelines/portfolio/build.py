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
    "아래 개발자의 GitHub 활동 근거만 보고 "
    "소개 2~3문장과 선정된 프로젝트별 기여 요약 1문장을 써라. "
    "소개는 intro, 프로젝트 요약은 projects 에 넣는다. "
    "프로젝트 문장은 해당 repo:{owner/name} id를 evidence_ids에 포함한다. "
    "근거가 부족하면 문장 수를 줄인다. 없는 사실을 만들지 않는다."
)


class _Draft(BaseModel):
    intro: list[guard.GroundedText] = Field(default_factory=list)
    projects: list[guard.GroundedText] = Field(default_factory=list)


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
    intro, summaries = await _draft(view, llm)
    summary_md, summary_refs = sections.summary(view, intro)
    stats_md, stats_refs = sections.stats(view)
    skills_md, skills_refs = sections.skills(view)
    projects_md, projects_refs = sections.projects(view, summaries)

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


async def _draft(
    evidence: Evidence, llm: Any | None
) -> tuple[list[guard.GroundedText], dict[str, guard.GroundedText]]:
    if evidence.is_empty():
        return [], {}
    result = await guard.complete(_Draft, instruction=INSTRUCTION, evidence=evidence, llm=llm)
    if result is None:
        return [], {}
    allowed = evidence.ids()
    intro = guard.keep_grounded(result.intro, allowed)
    project_ids = {p.id for p in evidence.projects}
    summaries: dict[str, guard.GroundedText] = {}
    for item in guard.keep_grounded(result.projects, allowed):
        repo_ids = [sid for sid in item.evidence_ids if sid in project_ids]
        if len(repo_ids) != 1:
            continue
        summaries[repo_ids[0]] = item
    return intro, summaries


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
