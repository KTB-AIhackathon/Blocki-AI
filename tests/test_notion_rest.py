from __future__ import annotations

import json

import httpx
import pytest

from app.publish.notion_mcp import open_session
from app.publish.notion_rest import RestSession, is_integration_token, title_properties


def test_portal_tokens_are_integration_tokens() -> None:
    assert is_integration_token("ntn_abc")
    assert is_integration_token("secret_abc")
    assert not is_integration_token("eyJabc")


@pytest.mark.asyncio
async def test_open_session_sends_integration_tokens_to_the_notion_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, str] = {}

    async def fake_open(token: str) -> RestSession:
        seen["token"] = token
        return RestSession(httpx.AsyncClient())

    monkeypatch.setattr("app.publish.notion_rest.open_rest_session", fake_open)
    session = await open_session("ntn_live_token")
    assert seen["token"] == "ntn_live_token"
    assert isinstance(session, RestSession)


@pytest.mark.asyncio
async def test_rest_create_omits_parent_at_the_private_root() -> None:
    bodies: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request.content.decode())
        return httpx.Response(
            200,
            json={"id": "page-1", "url": "https://notion.so/page-1"},
        )

    session = RestSession(
        httpx.AsyncClient(
            base_url="https://api.notion.com/v1",
            transport=httpx.MockTransport(handler),
        )
    )
    page_id, url = await session.create_page(
        title="Developer TIL Dashboard",
        markdown="hello",
        parent_id=None,
        icon="🧑‍💻",
    )
    assert (page_id, url) == ("page-1", "https://notion.so/page-1")
    assert any('"workspace": true' in body or '"workspace":true' in body for body in bodies)
    assert any("Developer TIL Dashboard" in body for body in bodies)


@pytest.mark.asyncio
async def test_rest_create_sends_rich_text_title_and_patches_it_without_h1() -> None:
    calls: list[tuple[str, str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(
            (request.method, request.url.path, json.loads(request.content.decode() or "{}"))
        )
        return httpx.Response(200, json={"id": "page-2", "url": "https://notion.so/page-2"})

    session = RestSession(
        httpx.AsyncClient(
            base_url="https://api.notion.com/v1",
            transport=httpx.MockTransport(handler),
        )
    )
    await session.create_page(
        title="포트폴리오 2026-08-20",
        markdown="본문",
        parent_id="3c216da5-521c-8109-b6c7-e8ca87f0f3c9",
    )
    assert calls[0][0] == "POST"
    assert calls[0][2]["properties"] == title_properties("포트폴리오 2026-08-20")
    assert calls[0][2]["markdown"] == "본문"
    assert calls[1] == (
        "PATCH",
        "/v1/pages/page-2",
        {"properties": title_properties("포트폴리오 2026-08-20")},
    )
    assert all("# 포트폴리오 2026-08-20" not in json.dumps(body) for _method, _path, body in calls)


@pytest.mark.asyncio
async def test_rest_update_writes_markdown_and_title_separately() -> None:
    paths: list[str] = []
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "page-3"})

    session = RestSession(
        httpx.AsyncClient(
            base_url="https://api.notion.com/v1",
            transport=httpx.MockTransport(handler),
        )
    )

    await session.update_page("page-3", "본문", title="새 제목")

    assert paths == ["/v1/pages/page-3/markdown", "/v1/pages/page-3"]
    assert bodies[0]["replace_content"]["new_str"] == "본문"
    assert bodies[1] == {"properties": title_properties("새 제목")}


@pytest.mark.asyncio
async def test_rest_create_falls_back_to_page_patch_when_properties_are_rejected() -> None:
    paths: list[str] = []
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        bodies.append(json.loads(request.content))
        if len(paths) == 1:
            return httpx.Response(400, json={"message": "properties unsupported"})
        return httpx.Response(200, json={"id": "page-4", "url": "https://notion.so/page-4"})

    session = RestSession(
        httpx.AsyncClient(
            base_url="https://api.notion.com/v1",
            transport=httpx.MockTransport(handler),
        )
    )

    page_id, _ = await session.create_page(title="제목", markdown="본문", parent_id=None)

    assert page_id == "page-4"
    assert paths == ["/v1/pages", "/v1/pages", "/v1/pages/page-4"]
    assert "properties" not in bodies[1]
    assert bodies[2] == {"properties": title_properties("제목")}
