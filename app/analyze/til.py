from __future__ import annotations

import re

from app.contracts import NotionSnapshot, TilFact

from app.contracts.evidence import MetricFact

# 템플릿은 `- **키:**` 로 안내하지만 실제 페이지는 볼드가 벗겨진 채 돌아오고, 사용자가
# 편집하는 동안 불릿도 -, *, •, · 로 섞인다. 표기를 강제하면 본문 전체를 놓친다.
_BULLET_MARK = r"[-*•·]"
_BULLET = re.compile(
    rf"^(?P<indent>\s*){_BULLET_MARK}\s*(?:\[[ xX]\]\s*)?"
    rf"\*{{0,2}}(?P<key>[^*:：]+?)\s*[:：]\*{{0,2}}\s*(?P<value>.*?)\s*$"
)
# 키 줄이 비고 값이 아래 들여쓰기 줄에 오는 형태. 번호 목록도 값으로 본다.
_CONTINUATION = re.compile(rf"^(?P<indent>\s*)(?:{_BULLET_MARK}|\d+\.)\s*(?P<value>.+?)\s*$")
_GROUPS = {
    "problem": ("문제 또는 목표", "문제", "원인"),
    "attempt": ("내가 한 일", "선택한 방법과 이유", "시도"),
    "result": ("결과", "최종 해결"),
    "learned": ("배운 내용", "기존 이해와 달라진 점"),
}
_METRIC_KEYS = {"Before": "before", "After": "after", "단위": "unit", "측정 기준": "criterion"}
_TABLE_REPO = re.compile(
    r"\|\s*Repository\s*\|\s*(https?://github\.com/[^\s|]+)",
    re.IGNORECASE,
)
# 기본 정보 표의 프로젝트 행. 저장소 URL 이 비거나 다른 저장소를 가리켜도 이 이름이
# 저장소 설명과 겹치면 붙는다. 사용자는 여기에 서비스 이름을 적지 저장소 경로를 적지 않는다.
_TABLE_PROJECT = re.compile(r"\|\s*프로젝트\s*\|\s*([^|]+?)\s*\|")


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
        work_repo=_first(values, "Repository") or _table_repo(entry.body_markdown),
        project_name=_first(values, "프로젝트") or _table_project(entry.body_markdown),
    )


def _values(body: str) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    open_key: str | None = None
    open_indent = ""
    for line in body.splitlines():
        match = _BULLET.match(line)
        if match:
            key = match.group("key").strip()
            value = match.group("value").strip()
            if value:
                values.setdefault(key, []).append(value)
                open_key = None
            else:
                # 값이 아래 줄에 있다. 더 깊이 들여쓴 줄만 이 키의 것으로 본다.
                open_key, open_indent = key, match.group("indent")
            continue
        if open_key is None:
            continue
        deeper = _CONTINUATION.match(line)
        # 같은 깊이도 받는다. 노션이 내보낼 때 하위 항목의 들여쓰기가 사라지는 경우가 있고,
        # 키 줄이 아닌 불릿은 _BULLET 이 이미 걸러 냈으므로 남은 것은 이 키의 값뿐이다.
        if deeper and len(deeper.group("indent")) >= len(open_indent):
            values.setdefault(open_key, []).append(deeper.group("value"))
        elif line.strip():
            open_key = None
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


def _table_repo(body: str) -> str:
    match = _TABLE_REPO.search(body)
    return match.group(1).strip() if match else ""


def _table_project(body: str) -> str:
    match = _TABLE_PROJECT.search(body)
    return match.group(1).strip() if match else ""


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
