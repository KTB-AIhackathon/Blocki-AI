from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class TilEntry(BaseModel):
    date: date
    title: str
    body_markdown: str
    page_id: str
    tags: list[str] = Field(default_factory=list)


class NotionSnapshot(BaseModel):
    entries: list[TilEntry] = Field(default_factory=list)
    complete: bool
    warnings: list[str] = Field(default_factory=list)

