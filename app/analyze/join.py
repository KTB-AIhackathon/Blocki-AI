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

        strong = _strongest(projects, entry)
        if strong is not None:
            strong.til.append(entry)
            continue

        weak = [project for project in projects if _in_period(project, entry)]
        if len(weak) == 1:
            weak[0].til.append(entry)
        else:
            unmatched.append(entry)
    return unmatched


def _strongest(projects: list[ProjectFacts], entry: TilFact) -> ProjectFacts | None:
    query = _tokens(" ".join((entry.title, *entry.tags)))
    if not query:
        return None

    ranked: list[tuple[int, int, int, int, ProjectFacts]] = []
    for index, project in enumerate(projects):
        description = _tokens(project.description or "")
        metadata = _tokens(" ".join((project.repo, *project.topics)))
        description_overlap = len(query & description)
        metadata_overlap = len(query & metadata)
        total_overlap = len(query & (description | metadata))
        if total_overlap:
            ranked.append(
                (
                    description_overlap * 3 + metadata_overlap,
                    description_overlap,
                    total_overlap,
                    -index,
                    project,
                )
            )
    if not ranked:
        return None
    return max(ranked, key=lambda item: item[:-1])[-1]


def _in_period(project: ProjectFacts, entry: TilFact) -> bool:
    if project.started_at is None or project.ended_at is None:
        return False
    return project.started_at.date() <= entry.date <= project.ended_at.date()


def _tokens(value: str) -> set[str]:
    return {token.casefold() for token in _TOKENS.findall(value) if len(token) >= 2}
