from __future__ import annotations

import re
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
    "EVIDENCE와 아래 주요 작업 카드의 키워드만 보고 소개를 한국어로 쓴다. "
    "대괄호 persona 한 줄과 강점 3개를 반환한다. 각 강점은 굵은 라벨과 한두 문장이다. "
    "저장소 이름, 커밋 제목, 커밋·PR·이슈 개수는 소개에 나열하지 않는다. "
    "성격이나 미션 문장은 쓰지 않는다. 각 문장은 근거 id를 함께 반환한다."
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
    readme = _profile_readme(snapshot)
    skills_md, skills_refs = sections.skills(evidence, _readme_block(readme.get("기술")))
    projects_md, projects_refs = sections.projects(evidence)
    learning_md, learning_refs = sections.learning(evidence)
    experience_md, experience_missing = _profile_field(
        fields.experience_md, readme.get("경력"), "experience_md"
    )
    education_md, education_missing = _profile_field(
        fields.education_md, readme.get("학력"), "education_md"
    )
    selection_md, selection_refs = common.selection(evidence, evidence.projects)

    body = render.render(
        KIND,
        version,
        {
            "name": fields.name,
            "contact_md": fields.contact_md.strip() or common.FILL_IN,
            "summary_md": summary_md,
            "skills_md": skills_md,
            "projects_md": projects_md,
            "learning_md": learning_md,
            "experience_md": experience_md,
            "education_md": education_md,
            "selection_md": selection_md,
        },
    )

    unresolved = [
        field
        for field, value in (
            ("skills_md", skills_md if readme.get("기술") else ""),
            ("projects_md", projects_md),
            ("contact_md", fields.contact_md.strip()),
        )
        if not value
    ]
    unresolved.extend(experience_missing + education_missing)
    refs: list[EvidenceRef] = [
        *summary_refs,
        *skills_refs,
        *projects_refs,
        *learning_refs,
        *selection_refs,
    ]
    complete = snapshot.complete and evidence.complete and not unresolved
    return ArtifactProposal(
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


async def _intro_lines(evidence: Evidence, llm: Any | None) -> list[str]:
    if evidence.is_empty():
        return []
    result = await guard.complete(_Intro, instruction=INSTRUCTION, evidence=evidence, llm=llm)
    raw = [] if result is None else guard.keep_grounded(result.sentences, evidence.ids())
    texts = [_safe_intro_text(item.text, evidence) for item in raw]
    texts = [text for text in texts if text]
    persona = next((text.strip("* ") for text in texts if text.startswith("[")), "프로젝트를 구현하고 개선하는 개발자")
    strengths = [text for text in texts if text != persona][:3]
    strengths.extend(_fallback_strengths(evidence, len(strengths)))
    return [
        f"**[{persona.strip('[]')}]**",
        f"**문제 구조화** : {strengths[0]}",
        f"**도구화와 구현** : {strengths[1]}",
        f"**측정으로 개선 증명** : {strengths[2]}",
    ]


def _safe_intro_text(text: str, evidence: Evidence) -> str:
    value = " ".join((text or "").split()).strip()
    if not value or any(char.isdigit() for char in value):
        return ""
    forbidden = [project.repo.casefold() for project in evidence.projects]
    forbidden.extend(project.repo.rsplit("/", 1)[-1].casefold() for project in evidence.projects)
    forbidden.extend(item.subject.casefold() for project in evidence.projects for item in project.highlights)
    lowered = value.casefold()
    return "" if any(item and item in lowered for item in forbidden) else value


def _fallback_strengths(evidence: Evidence, used: int) -> list[str]:
    options = [
        "문제와 목표를 기록으로 정리하고 작업의 방향을 세웁니다.",
        "필요한 기능을 도구로 구현하고 반복 작업을 줄입니다.",
        "결과와 측정 기준을 남겨 개선의 근거를 확인합니다.",
    ]
    return options[used:3]


def _profile_readme(snapshot: GitHubSnapshot) -> dict[str, str]:
    login = (snapshot.viewer_login or "").strip().casefold()
    if not login:
        return {}
    profile = next(
        (repo.readme.content for repo in snapshot.repos if repo.name.casefold() == login and repo.readme),
        "",
    )
    if not profile:
        return {}
    headings = list(re.finditer(r"^##\s+(.+?)\s*$", profile, re.MULTILINE))
    found: dict[str, str] = {}
    for index, match in enumerate(headings):
        title = match.group(1).strip()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(profile)
        content = profile[match.end() : end].strip()
        if title in {"경력", "학력", "기술"} and content:
            found[title] = content
    return found


def _readme_block(content: str | None) -> str:
    if not content:
        return ""
    return "(자동 초안 — 확인해 주세요)\n\n" + content.strip()


def _profile_field(value: str, readme: str | None, field: str) -> tuple[str, list[str]]:
    if value.strip():
        return value.strip(), []
    drafted = _readme_block(readme)
    return drafted or common.FILL_IN, [] if drafted else [field]
