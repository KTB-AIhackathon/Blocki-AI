"""POST /internal/notion/dashboard — the contract Spring codes against."""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.publish import notion_dashboard as dash
from app.publish.notion_template import CHILD_PAGES, DASHBOARD_TITLE
from tests.conftest import NOTION_TOKEN
from tests.notion_double import NotionWorkspace, mcp_session

KEY = "dev-internal-key"
HEADERS = {"X-Internal-Key": KEY, "X-Notion-Token": NOTION_TOKEN}
PATH = "/internal/notion/dashboard"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("INTERNAL_API_KEY", KEY)
    return TestClient(create_app())


@pytest.fixture
def workspace(monkeypatch: pytest.MonkeyPatch) -> NotionWorkspace:
    live = NotionWorkspace()

    async def open_session(token: str):
        assert token == NOTION_TOKEN, "the header token must reach the transport"
        return mcp_session(live)

    monkeypatch.setattr("app.publish.notion.open_session", open_session)
    return live


def test_the_first_connect_builds_the_tree_and_returns_the_page_id(
    client: TestClient, workspace: NotionWorkspace
) -> None:
    response = client.post(PATH, json={"user_id": "u1"}, headers=HEADERS)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["created"] is True
    assert body["page_id"] == workspace.dashboard_id
    assert workspace.titles_under(body["page_id"]) == [child.title for child in CHILD_PAGES]


def test_reconnecting_returns_the_same_page(
    client: TestClient, workspace: NotionWorkspace
) -> None:
    first = client.post(PATH, json={"user_id": "u1"}, headers=HEADERS).json()

    second = client.post(
        PATH, json={"user_id": "u1", "known_page_id": first["page_id"]}, headers=HEADERS
    ).json()

    assert (second["page_id"], second["created"]) == (first["page_id"], False)
    assert [page["title"] for page in workspace.pages].count(DASHBOARD_TITLE) == 1


def test_a_missing_token_is_reported_not_guessed(client: TestClient) -> None:
    response = client.post(PATH, json={"user_id": "u1"}, headers={"X-Internal-Key": KEY})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["error"] == {
        "code": "validation",
        "message": "Notion token header is required",
        "retryable": False,
    }


def test_a_notion_outage_is_retryable_and_scrubbed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def open_session(_token: str):
        raise RuntimeError(f"503 from notion for {NOTION_TOKEN}")

    monkeypatch.setattr("app.publish.notion.open_session", open_session)
    with caplog.at_level(logging.ERROR, logger="app.api.notion"):
        response = client.post(PATH, json={"user_id": "u1"}, headers=HEADERS)

    body = response.json()
    assert body["ok"] is False
    assert body["error"]["retryable"] is True
    assert NOTION_TOKEN not in response.text
    assert "notion dashboard error=internal" in caplog.text
    assert "«redacted»" in caplog.text
    assert NOTION_TOKEN not in caplog.text


def test_a_root_403_is_a_non_retryable_validation_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def denied(**_kwargs):
        raise dash.DashboardRestricted(dash.PAGE_ACCESS_HINT)

    monkeypatch.setattr("app.api.notion.ensure_dashboard_page", denied)
    response = client.post(PATH, json={"user_id": "u1"}, headers=HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["error"] == {
        "code": "validation",
        "message": dash.PAGE_ACCESS_HINT,
        "retryable": False,
    }


def test_the_endpoint_needs_the_internal_key(client: TestClient) -> None:
    response = client.post(PATH, json={"user_id": "u1"}, headers={"X-Notion-Token": NOTION_TOKEN})
    assert response.status_code == 401
