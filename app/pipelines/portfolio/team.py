"""Portfolio specialist roles. Functions, not a generic agent runtime."""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from pydantic import BaseModel, Field

from app.contracts import Evidence, ProjectFacts
from app.llm import guard
from app.llm.guard import GroundedText
from app.pipelines.portfolio import briefs

MAX_FEATURED = 3
SELECT_CAP = 30.0
FILL_CAP = 40.0
WRITE_CAP = 40.0
PUBLISH_RESERVE = 20.0
SELECT_FLOOR = 12.0
FILL_FLOOR = 12.0
WRITE_FLOOR = 15.0
WORK_PREFIXES = ("commit:", "pr:", "issue:", "til:")

SELECT_INSTRUCTION = (
    "폴더 사실만 보고 포트폴리오에 넣을 저장소를 고른다. "
    "selected_ids는 존재하는 repo: id만, 1~3개, 문서에 나올 순서. "
    "없는 성과를 만들지 않는다. 일이 보이는 쪽을 고른다. "
    "빈 폴더를 억지로 채우지 않는다. "
    "동점이면 기술이나 팀/개인을 섞는다. 점수는 마지막이다."
)
PITCH_INSTRUCTION = (
    "FORM은 빈 카드 모양이다. MATERIALS의 sheet만 보고 이 카드의 한두 줄을 pitch에 쓴다. "
    "시트에 있는 사실만 쓴다. 수치나 성격을 만들지 않는다. "
    "각 문장에 실존 evidence_ids를 단다."
)
WORK_INSTRUCTION = (
    "FORM은 빈 카드의 주요 작업 칸이다. MATERIALS의 sheet와 work_ids만 보고 "
    "화면에 넣을 id를 work_ids에 최대 3개 고른다. "
    "문장을 만들지 않는다. commit·pr·issue·til id만 넣는다."
)
WRITE_INSTRUCTION = (
    "FORM은 소개 칸이다. 고른 카드의 sheet, 한 줄, 작업 제목만 보고 intro를 한국어로 쓴다. "
    "시트에 있는 사실만 쓴다. 성격이나 미션 문장은 쓰지 않는다. "
    "고르지 않은 저장소를 인용하지 않는다. "
    "수치를 만들지 않는다. 각 문장에 evidence_ids를 넣는다. "
    "시트를 다시 쓰지 않는다. 작업 불릿을 다시 쓰지 않는다."
)
INTRO_FORM = "## 소개\n\n"


class Folder(BaseModel):
    project_id: str
    score: float = 0.0
    score_breakdown: dict[str, float] = Field(default_factory=dict)
    stack: list[str] = Field(default_factory=list)
    team: bool = False
    density: int = 0
    description: str | None = None
    form: str = ""
    work_titles: list[dict[str, str]] = Field(default_factory=list)


class Dossier(BaseModel):
    project_id: str
    score: float = 0.0
    stack: list[str] = Field(default_factory=list)
    team: bool = False
    density: int = 0
    pitch: list[GroundedText] = Field(default_factory=list)
    work_ids: list[str] = Field(default_factory=list)


class _SelectDraft(BaseModel):
    selected_ids: list[str] = Field(default_factory=list)
    reason: str = ""


class _PitchDraft(BaseModel):
    pitch: list[GroundedText] = Field(default_factory=list)


class _WorkDraft(BaseModel):
    work_ids: list[str] = Field(default_factory=list)


class _WriteDraft(BaseModel):
    intro: list[GroundedText] = Field(default_factory=list)


def cap(deadline: float | None, want: float, reserve: float = 0.0) -> float:
    if deadline is None:
        return want
    return max(0.0, min(want, deadline - time.monotonic() - reserve))


def stack_of(project: ProjectFacts, evidence: Evidence) -> list[str]:
    names = [skill.name for skill in evidence.skills if project.repo in skill.repos]
    return names or [skill.name for skill in project.languages]


def density_of(project: ProjectFacts) -> int:
    filled = 0
    if (project.description or "").strip():
        filled += 1
    if project.highlights:
        filled += 1
    if project.pull_requests or project.issues:
        filled += 1
    if project.til:
        filled += 1
    return filled


def work_catalog(project: ProjectFacts) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for highlight in project.highlights:
        title = (highlight.subject or "").strip()
        if title:
            items.append({"id": highlight.id, "title": title})
    for item in [*project.pull_requests, *project.issues]:
        title = (item.title or "").strip()
        if title:
            items.append({"id": item.id, "title": title})
    for item in project.til:
        title = (item.title or "").strip()
        if title:
            items.append({"id": item.id, "title": title})
    return items


def _norm_title(title: str) -> str:
    text = re.sub(r"\s+", " ", title).casefold().strip()
    for prefix in ("feat:", "fix:"):
        if text.startswith(prefix):
            text = text[len(prefix) :].strip()
            break
    return text


def default_work_ids(project: ProjectFacts) -> list[str]:
    catalog = work_catalog(project)
    by_id = {item["id"]: item["title"] for item in catalog}
    buckets = [
        [item.id for item in project.highlights if (item.subject or "").strip()],
        [
            item.id
            for item in (*project.pull_requests, *project.issues)
            if (item.title or "").strip()
        ],
        [item.id for item in project.til if (item.title or "").strip()],
    ]
    chosen: list[str] = []
    seen: set[str] = set()

    def take(source_id: str) -> bool:
        title = by_id.get(source_id, "")
        key = _norm_title(title)
        if not title or key in seen:
            return False
        seen.add(key)
        chosen.append(source_id)
        return True

    for bucket in buckets:
        for source_id in bucket:
            if take(source_id):
                break
        if len(chosen) >= 3:
            return chosen
    for item in catalog:
        if item["id"] in chosen:
            continue
        if take(item["id"]) and len(chosen) >= 3:
            return chosen
    return chosen


def card_form(project: ProjectFacts, featured: list[str]) -> str:
    short = project.repo.rsplit("/", 1)[-1]
    label = project.repo if sum(1 for repo in featured if repo.rsplit("/", 1)[-1] == short) > 1 else short
    return (
        f"### {label}\n\n"
        f"> \n\n"
        f"- 기술:\n"
        f"- 저장소:\n\n"
        f"**주요 작업**\n"
        f"-\n"
    )


def make_folders(evidence: Evidence) -> list[Folder]:
    featured = [project.repo for project in evidence.projects]
    return [
        Folder(
            project_id=project.id,
            score=project.score,
            score_breakdown=project.score_breakdown,
            stack=stack_of(project, evidence),
            team=project.team,
            density=density_of(project),
            description=project.description,
            form=card_form(project, featured),
            work_titles=work_catalog(project),
        )
        for project in evidence.projects
    ]


def sheets_by_id(
    evidence: Evidence, sheets: list[dict[str, str]] | None
) -> dict[str, str]:
    items = sheets if sheets is not None else briefs.render_briefs(evidence)
    return {
        project.id: item["markdown"]
        for project, item in zip(evidence.projects, items)
    }


def project_digest(project: ProjectFacts, sheet: str) -> dict[str, Any]:
    return {
        "id": project.id,
        "repo": project.repo,
        "sheet": sheet,
        "work_ids": work_catalog(project),
    }


def folders_digest(folders: list[Folder]) -> dict[str, Any]:
    return {
        "projects": [
            {
                "id": folder.project_id,
                "score": folder.score,
                "score_breakdown": folder.score_breakdown,
                "stack": folder.stack,
                "team": folder.team,
                "density": folder.density,
                "description": folder.description,
                "work_titles": folder.work_titles,
            }
            for folder in folders
        ]
    }


def write_digest(
    evidence: Evidence,
    dossiers: list[Dossier],
    selected_ids: list[str],
    sheets: dict[str, str],
) -> dict[str, Any]:
    chosen = {dossier.project_id: dossier for dossier in dossiers}
    catalog = {project.id: work_catalog(project) for project in evidence.projects}
    return {
        "selected": [
            {
                "id": project.id,
                "repo": project.repo,
                "sheet": sheets.get(project.id, ""),
                "pitch": [
                    item.model_dump()
                    for item in chosen.get(project.id, Dossier(project_id=project.id)).pitch
                ],
                "chosen_work": [
                    item
                    for item in catalog.get(project.id, [])
                    if item["id"]
                    in chosen.get(project.id, Dossier(project_id=project.id)).work_ids
                ],
            }
            for project in evidence.projects
            if project.id in selected_ids
        ]
    }


def score_ids(folders: list[Folder], limit: int = MAX_FEATURED) -> list[str]:
    ranked = sorted(folders, key=lambda item: (-item.score, item.project_id))
    return [item.project_id for item in ranked[:limit]]


def sanitize_ids(picked: list[str], folders: list[Folder]) -> list[str]:
    known = {item.project_id for item in folders}
    chosen: list[str] = []
    for item in picked:
        if item in known and item not in chosen:
            chosen.append(item)
        if len(chosen) >= MAX_FEATURED:
            return chosen
    honest = bool(picked) and all(item in known for item in picked)
    if honest:
        return chosen
    for item in score_ids(folders, limit=len(folders)):
        if item not in chosen:
            chosen.append(item)
        if len(chosen) >= MAX_FEATURED:
            break
    return chosen


def view_of(evidence: Evidence, selected_ids: list[str]) -> Evidence:
    by_id = {project.id: project for project in evidence.projects}
    chosen = [by_id[item] for item in selected_ids if item in by_id]
    if not chosen:
        chosen = evidence.projects[:MAX_FEATURED]
    if len(chosen) == len(evidence.projects) and [p.id for p in evidence.projects] == [
        p.id for p in chosen
    ]:
        return evidence
    starts = [project.started_at for project in chosen if project.started_at]
    ends = [project.ended_at for project in chosen if project.ended_at]
    return evidence.model_copy(
        update={
            "projects": chosen,
            "my_commits": sum(project.my_commits for project in chosen),
            "period_start": min(starts) if starts else evidence.period_start,
            "period_end": max(ends) if ends else evidence.period_end,
        }
    )


def sanitize_work_ids(picked: list[str], project: ProjectFacts) -> list[str]:
    allowed = {item["id"]: item["title"] for item in work_catalog(project)}
    chosen: list[str] = []
    seen: set[str] = set()
    for item in picked:
        title = allowed.get(item)
        if title is None or not item.startswith(WORK_PREFIXES) or item in chosen:
            continue
        key = _norm_title(title)
        if key in seen:
            continue
        seen.add(key)
        chosen.append(item)
        if len(chosen) >= 3:
            return chosen
    return chosen or default_work_ids(project)


def fallback_card(folder: Folder, project: ProjectFacts) -> Dossier:
    return Dossier(
        project_id=folder.project_id,
        score=folder.score,
        stack=folder.stack,
        team=folder.team,
        density=folder.density,
        work_ids=default_work_ids(project),
    )


async def select_ids(
    folders: list[Folder], llm: Any | None, *, timeout: float
) -> tuple[list[str], str]:
    fallback = score_ids(folders)
    if len(folders) < 2 or timeout < SELECT_FLOOR:
        return (fallback[: len(folders)] if len(folders) < 2 else fallback), ""
    result = await guard.complete(
        _SelectDraft,
        instruction=SELECT_INSTRUCTION,
        digest=folders_digest(folders),
        timeout=timeout,
        llm=llm,
    )
    if result is None:
        return fallback, ""
    chosen = sanitize_ids(result.selected_ids, folders)
    return chosen or fallback, result.reason.strip()


async def fill_card(
    project: ProjectFacts,
    folder: Folder,
    evidence: Evidence,
    llm: Any | None,
    *,
    timeout: float,
    sheet: str,
) -> Dossier:
    card = fallback_card(folder, project)
    if timeout < FILL_FLOOR:
        return card
    digest = project_digest(project, sheet)
    pitch_task = guard.complete(
        _PitchDraft,
        instruction=PITCH_INSTRUCTION,
        digest=digest,
        extra={"form": folder.form},
        timeout=timeout,
        llm=llm,
    )
    work_task = guard.complete(
        _WorkDraft,
        instruction=WORK_INSTRUCTION,
        digest=digest,
        extra={"form": folder.form},
        timeout=timeout,
        llm=llm,
    )
    pitch_result, work_result = await asyncio.gather(pitch_task, work_task)
    if pitch_result is not None:
        card.pitch = guard.keep_grounded(pitch_result.pitch, evidence.ids())[:2]
    if work_result is not None:
        card.work_ids = sanitize_work_ids(work_result.work_ids, project)
    return card


async def fill_cards(
    evidence: Evidence,
    folders: list[Folder],
    selected_ids: list[str],
    llm: Any | None,
    *,
    timeout: float,
    sheets: dict[str, str],
) -> list[Dossier]:
    by_id = {project.id: project for project in evidence.projects}
    by_folder = {folder.project_id: folder for folder in folders}
    memory: dict[str, Dossier] = {}

    async def one(project_id: str) -> None:
        project = by_id[project_id]
        folder = by_folder[project_id]
        memory[project_id] = await fill_card(
            project,
            folder,
            evidence,
            llm,
            timeout=timeout,
            sheet=sheets.get(project_id, ""),
        )

    outcomes = await asyncio.gather(
        *(one(project_id) for project_id in selected_ids if project_id in by_id),
        return_exceptions=True,
    )
    for outcome in outcomes:
        if isinstance(outcome, BaseException):
            raise outcome
    return [memory[project_id] for project_id in selected_ids if project_id in memory]


async def write_intro(
    evidence: Evidence,
    dossiers: list[Dossier],
    selected_ids: list[str],
    llm: Any | None,
    *,
    timeout: float,
    sheets: dict[str, str],
) -> list[GroundedText]:
    if evidence.is_empty() or timeout < WRITE_FLOOR:
        return []
    result = await guard.complete(
        _WriteDraft,
        instruction=WRITE_INSTRUCTION,
        digest=write_digest(evidence, dossiers, selected_ids, sheets),
        extra={"form": INTRO_FORM},
        timeout=timeout,
        llm=llm,
    )
    if result is None:
        return []
    return guard.keep_grounded(result.intro, evidence.ids())


async def run_team(
    evidence: Evidence,
    llm: Any | None,
    *,
    deadline: float | None = None,
    sheets: list[dict[str, str]] | None = None,
) -> tuple[list[str], list[Dossier], list[GroundedText], str]:
    folders = make_folders(evidence)
    mapped = sheets_by_id(evidence, sheets)
    select_timeout = cap(
        deadline, SELECT_CAP, reserve=FILL_FLOOR + WRITE_FLOOR + PUBLISH_RESERVE
    )
    selected_ids, reason = await select_ids(folders, llm, timeout=select_timeout)
    view = view_of(evidence, selected_ids)
    fill_timeout = cap(deadline, FILL_CAP, reserve=WRITE_FLOOR + PUBLISH_RESERVE)
    dossiers = await fill_cards(
        view, folders, selected_ids, llm, timeout=fill_timeout, sheets=mapped
    )
    write_timeout = cap(deadline, WRITE_CAP, reserve=PUBLISH_RESERVE)
    intro = await write_intro(
        view, dossiers, selected_ids, llm, timeout=write_timeout, sheets=mapped
    )
    return selected_ids, dossiers, intro, reason
