"""Join Notion learning records to repository facts."""

from __future__ import annotations

import re

from app.contracts import ProjectFacts, TilFact

_TOKENS = re.compile(r"[^\W_]+", re.UNICODE)


def attach(projects: list[ProjectFacts], til: list[TilFact]) -> list[TilFact]:
    for project in projects:
        project.til = []

    unmatched: list[TilFact] = []
    for entry in til:
        linked = _repo_from_work_repo(projects, entry)
        if linked is not None:
            linked.til.append(entry)
            continue
        confirmed = [
            project
            for project in projects
            if project.repo.casefold() in entry.body_markdown.casefold()
            and f"github.com/{project.repo}".casefold()
            in entry.body_markdown.casefold()
        ]
        if confirmed:
            confirmed[0].til.append(entry)
            continue

        named = _project_from_name(projects, entry)
        if named is not None:
            named.til.append(entry)
            continue

        strong = _strongest(projects, entry)
        if strong is not None:
            strong.til.append(entry)
        else:
            unmatched.append(entry)
    return unmatched


def _strongest(projects: list[ProjectFacts], entry: TilFact) -> ProjectFacts | None:
    query = _tokens(" ".join((entry.title, *entry.tags)))
    title = entry.title.casefold()
    body = entry.body_markdown.casefold()

    ranked: list[tuple[int, int, int, int, ProjectFacts]] = []
    for index, project in enumerate(projects):
        description = _tokens(project.description or "")
        metadata = _tokens(" ".join((project.repo, *project.topics)))
        description_overlap = len(query & description)
        metadata_overlap = len(query & metadata)
        short = project.repo.rsplit("/", 1)[-1].casefold()
        named = len(short) >= 3 and (short in title or short in body)
        if description_overlap < 1 and metadata_overlap < 2 and not named:
            continue
        ranked.append(
            (
                description_overlap * 3 + metadata_overlap + (3 if named else 0),
                description_overlap,
                metadata_overlap,
                -index,
                project,
            )
        )
    if not ranked:
        return None
    return max(ranked, key=lambda item: item[:-1])[-1]


def _project_from_name(
    projects: list[ProjectFacts], entry: TilFact
) -> ProjectFacts | None:
    """기본 정보 표의 프로젝트 이름으로 찾는다.

    사용자는 여기에 서비스 이름을 쓴다. 영문 저장소 이름과는 안 겹치지만 저장소
    설명에는 그 이름이 그대로 들어 있는 경우가 많다. 이모지와 장식이 섞여 오므로
    글자만 남겨 비교한다.
    """
    name = _letters(entry.project_name)
    if len(name) < 2:
        return None
    matches = [
        project
        for project in projects
        if name in _letters(project.description or "")
        or name in _letters(project.repo)
        or any(name in _letters(topic) for topic in project.topics)
    ]
    return matches[0] if len(matches) == 1 else None


def _letters(value: str) -> str:
    return "".join(_TOKENS.findall(value or "")).casefold()


def _repo_from_work_repo(projects: list[ProjectFacts], entry: TilFact) -> ProjectFacts | None:
    value = (entry.work_repo or "").casefold()
    if not value:
        return None
    matches = [project for project in projects if project.repo.casefold() in value]
    return matches[0] if len(matches) == 1 else None



def _tokens(value: str) -> set[str]:
    return {token.casefold() for token in _TOKENS.findall(value) if len(token) >= 2}
