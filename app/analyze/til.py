from __future__ import annotations

from app.contracts import NotionSnapshot, TilFact


def facts_of(snapshot: NotionSnapshot) -> list[TilFact]:
    return [
        TilFact(
            id=f"til:{entry.page_id}",
            date=entry.date,
            title=entry.title,
            body_markdown=entry.body_markdown,
            page_id=entry.page_id,
            tags=list(entry.tags),
        )
        for entry in sorted(snapshot.entries, key=lambda item: (item.date, item.page_id))
    ]


__all__ = ["facts_of"]

