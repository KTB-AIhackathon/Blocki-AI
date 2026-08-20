"""Notion API session for integration / personal access tokens.

Hosted MCP (`mcp.notion.com`) only accepts MCP OAuth tokens. A token minted
from the Notion developer portal (`ntn_` / `secret_`) talks to `api.notion.com`
instead, using the same markdown create/read/update surface the MCP tools wrap.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from app.publish.notion_schema import page_id_from_url

API_URL = os.environ.get("NOTION_API_URL", "https://api.notion.com/v1")
NOTION_VERSION = os.environ.get("NOTION_VERSION", "2026-03-11")


def is_integration_token(token: str) -> bool:
    return token.startswith(("ntn_", "secret_"))


class RestSession:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def create_page(
        self, *, title: str, markdown: str, parent_id: str | None, icon: str | None = None
    ) -> tuple[str | None, str | None]:
        body: dict[str, Any] = {
            "markdown": _with_title_heading(title, markdown),
        }
        if parent_id:
            body["parent"] = {"type": "page_id", "page_id": _page_id(parent_id)}
        else:
            body["parent"] = {"type": "workspace", "workspace": True}
        if icon:
            body["icon"] = {"type": "emoji", "emoji": icon}
        data = await self._request("POST", "/pages", json=body)
        return data.get("id"), data.get("url")

    async def read_page(self, target: str) -> Any:
        page_id = _page_id(target)
        page = await self._request("GET", f"/pages/{page_id}")
        markdown = await self._request("GET", f"/pages/{page_id}/markdown")
        return {
            "id": page_id,
            "title": _title_of(page),
            "url": page.get("url"),
            "text": markdown.get("markdown") or "",
        }

    async def update_page(self, page_id: str, markdown: str, title: str | None = None) -> tuple[str | None, str | None]:
        body = _with_title_heading(title, markdown) if title else (markdown or "")
        await self._request(
            "PATCH",
            f"/pages/{_page_id(page_id)}/markdown",
            json={
                "type": "replace_content",
                "replace_content": {"new_str": body},
            },
        )
        return page_id, None

    async def list_root_pages(self) -> list[dict[str, Any]]:
        """REST has no private-sidebar list. Do not fall back to workspace search."""
        return []

    async def search_pages(self, query: str) -> list[dict[str, Any]]:
        return []

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = await self._client.request(method, path, **kwargs)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = (exc.response.text or "")[:180]
            raise RuntimeError(f"notion api {exc.response.status_code} {path}: {detail}") from exc
        payload = response.json()
        return payload if isinstance(payload, dict) else {"data": payload}


async def open_rest_session(notion_token: str) -> RestSession:
    client = httpx.AsyncClient(
        base_url=API_URL.rstrip("/"),
        headers={
            "Authorization": f"Bearer {notion_token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        },
        timeout=60.0,
    )
    return RestSession(client)


def _page_id(target: str) -> str:
    return page_id_from_url(target) or target.strip()


def _with_title_heading(title: str, markdown: str) -> str:
    """Notion uses the first `#` heading as the page title when properties.title is omitted."""
    body = markdown or ""
    heading = f"# {title.strip()}".strip()
    stripped = body.lstrip()
    if stripped == heading or stripped.startswith(heading + "\n"):
        return body
    return f"{heading}\n\n{body}" if body.strip() else heading


def _title_of(page: dict[str, Any]) -> str | None:
    props = page.get("properties")
    if not isinstance(props, dict):
        return None
    title = props.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    if isinstance(title, dict):
        items = title.get("title")
        if isinstance(items, list):
            text = "".join(
                str(item.get("plain_text") or "")
                for item in items
                if isinstance(item, dict)
            )
            return text.strip() or None
    return None
