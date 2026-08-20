from __future__ import annotations

from datetime import date, datetime
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


class MetricFact(BaseModel):
    before: str = ""
    after: str = ""
    unit: str = ""
    criterion: str = ""

    def text(self) -> str:
        parts = []
        if self.before and self.after:
            parts.append(f"Before {self.before} → After {self.after}")
        elif self.before:
            parts.append(f"Before {self.before}")
        elif self.after:
            parts.append(f"After {self.after}")
        if self.unit:
            parts.append(f"단위: {self.unit}")
        if self.criterion:
            parts.append(f"측정 기준: {self.criterion}")
        return " · ".join(parts)


class WorkItem(BaseModel):
    id: str
    repo: str
    number: int
    title: str
    source_type: Literal["pr", "issue"]


class TilFact(BaseModel):
    id: str
    date: date
    title: str
    body_markdown: str
    page_id: str
    tags: list[str] = Field(default_factory=list)
    goal: str = ""
    problem: str = ""
    attempt: str = ""
    result: str = ""
    metric: MetricFact | None = None
    learned: str = ""
    retro: str = ""
    work_repo: str = ""

    def field_ids(self) -> set[str]:
        fields = ("goal", "problem", "attempt", "result", "metric", "learned", "retro", "work_repo")
        return {
            f"{self.id}:{field}"
            for field in fields
            if getattr(self, field) not in ("", None)
        }


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
    til: list[TilFact] = Field(default_factory=list)
    score: float = 0.0
    score_breakdown: dict[str, float] = Field(default_factory=dict)
    award: str | None = None

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
    til: list[TilFact] = Field(default_factory=list)
    unmatched_til: list[TilFact] = Field(default_factory=list)
    selection_candidates: list[ProjectFacts] = Field(default_factory=list)
    selection_reason: str = ""

    def model_dump(self, *args, **kwargs):
        dumped = super().model_dump(*args, **kwargs)
        if not self.unmatched_til:
            dumped.pop("unmatched_til", None)
        if not self.selection_candidates:
            dumped.pop("selection_candidates", None)
        if not self.selection_reason:
            dumped.pop("selection_reason", None)
        return dumped

    def ids(self) -> set[str]:
        projects = self.projects
        found = {p.id for p in projects}
        found |= {s.id for s in self.skills}
        for project in projects:
            found |= {c.id for c in project.highlights}
            found |= {s.id for s in project.languages}
            found |= {item.id for item in project.pull_requests}
            found |= {item.id for item in project.issues}
            found |= {item.id for item in project.til}
            for item in project.til:
                found |= item.field_ids()
        found |= {item.id for item in self.til}
        found |= {item.id for item in self.unmatched_til}
        for item in [*self.til, *self.unmatched_til]:
            found |= item.field_ids()
        return found

    def is_empty(self) -> bool:
        return not self.projects and not self.skills and not self.til
