from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

SkillCategory = Literal["language", "framework", "database", "infra", "tool"]
ChangeType = Literal["feat", "fix", "perf", "refactor", "test", "docs", "build", "other"]


class EvidenceRef(BaseModel):
    """Where a rendered field came from. Returned to Spring for auditing."""

    field: str
    repo: str
    source_type: str
    source_id: str


class ViewerIdentity(BaseModel):
    login: str | None = None
    aliases: list[str] = Field(default_factory=list)

    def owns(self, author: str | None, email: str | None = None) -> bool:
        for candidate in (author, email):
            if candidate and candidate.strip().casefold() in self.aliases:
                return True
        return False


class SkillFact(BaseModel):
    id: str
    name: str
    category: SkillCategory
    weight: float = 0.0
    # True only when weight came from measured language bytes, so the renderer
    # knows whether it may show the weight as a percentage.
    measured: bool = False
    repos: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)


class CommitFact(BaseModel):
    id: str
    repo: str
    sha: str
    subject: str
    change_type: ChangeType = "other"
    committed_at: datetime | None = None


class WorkItem(BaseModel):
    id: str
    repo: str
    number: int
    title: str
    source_type: Literal["pr", "issue"]


class ProjectFacts(BaseModel):
    id: str
    repo: str
    url: str | None = None
    description: str | None = None
    topics: list[str] = Field(default_factory=list)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    my_commits: int = 0
    total_commits: int = 0
    contributors: int = 1
    merged_prs: int = 0
    closed_issues: int = 0
    languages: list[SkillFact] = Field(default_factory=list)
    highlights: list[CommitFact] = Field(default_factory=list)
    pull_requests: list[WorkItem] = Field(default_factory=list)
    issues: list[WorkItem] = Field(default_factory=list)
    score: float = 0.0

    @property
    def team(self) -> bool:
        return self.contributors > 1


class Evidence(BaseModel):
    """Facts extracted from a snapshot. No prose, no inference beyond counting."""

    viewer: ViewerIdentity = Field(default_factory=ViewerIdentity)
    projects: list[ProjectFacts] = Field(default_factory=list)
    skills: list[SkillFact] = Field(default_factory=list)
    period_start: datetime | None = None
    period_end: datetime | None = None
    my_commits: int = 0
    complete: bool = True
    warnings: list[str] = Field(default_factory=list)

    def ids(self) -> set[str]:
        found = {p.id for p in self.projects}
        found |= {s.id for s in self.skills}
        for project in self.projects:
            found |= {c.id for c in project.highlights}
            found |= {s.id for s in project.languages}
            found |= {item.id for item in project.pull_requests}
            found |= {item.id for item in project.issues}
        return found

    def is_empty(self) -> bool:
        return not self.projects and not self.skills
