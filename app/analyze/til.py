from __future__ import annotations

import re

from app.contracts import NotionSnapshot, TilFact

from app.contracts.evidence import MetricFact

_BULLET = re.compile(
    r"^\s*-\s*(?:\[[ xX]\]\s*)?\*\*(?P<key>[^*]+?)\s*:\*\*\s*(?P<value>.*)\s*$"
)
_GROUPS = {
    "problem": ("문제 또는 목표", "문제", "원인"),
    "attempt": ("내가 한 일", "선택한 방법과 이유", "시도"),
    "result": ("결과", "최종 해결"),
    "learned": ("배운 내용", "기존 이해와 달라진 점"),
}
_METRIC_KEYS = {"Before": "before", "After": "after", "단위": "unit", "측정 기준": "criterion"}


def facts_of(snapshot: NotionSnapshot) -> list[TilFact]:
    return [_fact_of(entry) for entry in sorted(snapshot.entries, key=lambda item: (item.date, item.page_id))]


def _fact_of(entry) -> TilFact:
    values = _values(entry.body_markdown)
    return TilFact(
        id=f"til:{entry.page_id}",
        date=entry.date,
        title=entry.title,
        body_markdown=entry.body_markdown,
        page_id=entry.page_id,
        tags=list(entry.tags),
        goal=_first(values, "오늘의 목표"),
        problem=_group(values, "problem"),
        attempt=_group(values, "attempt"),
        result=_group(values, "result"),
        metric=_metric(values),
        learned=_group(values, "learned"),
        retro=_first(values, "자유롭게 작성"),
        work_repo=_first(values, "Repository"),
    )


def _values(body: str) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    for line in body.splitlines():
        match = _BULLET.match(line)
        if not match:
            continue
        key = match.group("key").strip()
        value = match.group("value").strip()
        if value:
            values.setdefault(key, []).append(value)
    return values


def _first(values: dict[str, list[str]], key: str) -> str:
    return values.get(key, [""])[0]


def _group(values: dict[str, list[str]], field: str) -> str:
    return "\n".join(
        value
        for key in _GROUPS[field]
        for value in values.get(key, [])
        if value
    )


def _metric(values: dict[str, list[str]]) -> MetricFact | None:
    metric = {
        field: _first(values, key)
        for key, field in _METRIC_KEYS.items()
    }
    if any("측정하지 않음" in value for value in metric.values()):
        return None
    if not any(metric.values()):
        return None
    return MetricFact(**metric)


__all__ = ["facts_of"]
