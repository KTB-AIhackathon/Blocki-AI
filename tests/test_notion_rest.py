from __future__ import annotations

import httpx
import pytest

from app.publish.notion_mcp import open_session
from app.publish.notion_rest import RestSession, is_integration_token


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
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = request.content.decode()
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
    assert str(captured["path"]).endswith("/pages")
    assert '"workspace": true' in captured["body"] or '"workspace":true' in captured["body"]
    assert "Developer TIL Dashboard" in str(captured["body"])


@pytest.mark.asyncio
async def test_rest_create_keeps_dated_title_when_body_already_has_h1() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode()
        return httpx.Response(200, json={"id": "page-2", "url": "https://notion.so/page-2"})

    session = RestSession(
        httpx.AsyncClient(
            base_url="https://api.notion.com/v1",
            transport=httpx.MockTransport(handler),
        )
    )
    await session.create_page(
        title="포트폴리오 2026-08-20",
        markdown="# 김택규 포트폴리오\n\n본문",
        parent_id="3c216da5-521c-8109-b6c7-e8ca87f0f3c9",
    )
    assert captured["body"].index("# 포트폴리오 2026-08-20") < captured["body"].index(
        "# 김택규 포트폴리오"
    )
