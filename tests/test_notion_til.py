from __future__ import annotations

import asyncio
from datetime import date

import httpx

from app.collect.notion_til import collect_notion_til
from app.contracts import NotionSnapshot
from tests.conftest import NOTION_TOKEN


def test_collect_notion_til_reads_dated_child_pages_without_network() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/blocks/dashboard/children"):
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "page-a",
                            "type": "child_page",
                            "child_page": {"title": "2026-08-20 · 캐시 개선"},
                        },
                        {
                            "id": "not-a-til",
                            "type": "child_page",
                            "child_page": {"title": "일일 Developer TIL 템플릿"},
                        },
                    ],
                    "has_more": False,
                },
            )
        if "/blocks/" in request.url.path and request.url.path.endswith("/children"):
            return httpx.Response(200, json={"results": [], "has_more": False})
        if request.url.path.endswith("/pages/page-a"):
            return httpx.Response(
                200,
                json={
                    "id": "page-a",
                    "properties": {
                        "title": {
                            "title": [{"plain_text": "2026-08-20 · 캐시 개선"}]
                        },
                        "Tags": {
                            "multi_select": [{"name": "backend"}, {"name": "cache"}]
                        },
                    },
                },
            )
        if request.url.path.endswith("/pages/page-a/markdown"):
            return httpx.Response(200, json={"markdown": "## 결과\n\n응답 시간이 줄었다."})
        return httpx.Response(404, json={"message": "not found"})

    async def run() -> NotionSnapshot:
        async with httpx.AsyncClient(
            base_url="https://api.notion.com/v1",
            transport=httpx.MockTransport(handler),
        ) as client:
            return await collect_notion_til("dashboard", NOTION_TOKEN, client=client)

    import asyncio

    snapshot = asyncio.run(run())

    assert snapshot.complete is True
    assert snapshot.warnings == []
    assert len(snapshot.entries) == 1
    entry = snapshot.entries[0]
    assert entry.date == date(2026, 8, 20)
    assert entry.title == "캐시 개선"
    assert entry.body_markdown == "## 결과\n\n응답 시간이 줄었다."
    assert entry.page_id == "page-a"
    assert entry.tags == ["backend", "cache"]
    assert all(request.headers["authorization"] == f"Bearer {NOTION_TOKEN}" for request in seen)
    assert all(request.headers["notion-version"] == "2026-03-11" for request in seen)


def test_dashboard_example_pages_are_not_collected_as_the_users_til() -> None:
    """대시보드가 심어두는 예시 TIL은 날짜가 붙어 있어도 사용자의 기록이 아니다."""
    from app.publish.notion_template import EXAMPLE_TITLES

    titles = {f"page-{index}": title for index, title in enumerate(EXAMPLE_TITLES)}
    titles["page-real"] = "2026-08-10 · AI 성능테스트 1일차"

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/blocks/dashboard/children"):
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"id": page_id, "type": "child_page", "child_page": {"title": title}}
                        for page_id, title in titles.items()
                    ],
                    "has_more": False,
                },
            )
        if "/blocks/" in path and path.endswith("/children"):
            return httpx.Response(200, json={"results": [], "has_more": False})
        if path.endswith("/markdown"):
            return httpx.Response(200, json={"markdown": "본문"})
        page_id = path.rsplit("/", 1)[-1]
        if page_id in titles:
            return httpx.Response(
                200,
                json={
                    "id": page_id,
                    "properties": {"title": {"title": [{"plain_text": titles[page_id]}]}},
                },
            )
        return httpx.Response(404, json={"message": "not found"})

    async def run() -> NotionSnapshot:
        async with httpx.AsyncClient(
            base_url="https://api.notion.com/v1",
            transport=httpx.MockTransport(handler),
        ) as client:
            return await collect_notion_til("dashboard", NOTION_TOKEN, client=client)

    snapshot = asyncio.run(run())

    collected = [entry.title for entry in snapshot.entries]
    assert collected == ["AI 성능테스트 1일차"]
    assert not any("[예시]" in title for title in collected)
