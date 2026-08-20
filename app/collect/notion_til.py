"""Read dated TIL pages below a Notion page through the REST API."""

from __future__ import annotations

import os
import re
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any

import httpx

from app.contracts import NotionSnapshot, TilEntry

API_URL = os.environ.get("NOTION_API_URL", "https://api.notion.com/v1")
NOTION_VERSION = os.environ.get("NOTION_VERSION", "2026-03-11")

_DATE_IN_TITLE = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})(?:\s*[·|:/-]\s*(?P<title>.*))?$")
_DATE_IN_BODY = re.compile(r"(?:\||\*\*)?날짜(?:\||\*\*)?\s*[:|]\s*(\d{4}-\d{2}-\d{2})")
_TAG_KEYS = {"tag", "tags", "태그"}
# 대시보드가 링크 동작을 보여주려고 만드는 예시 TIL은 날짜가 붙어 있어 그냥 두면
# 사용자가 하지도 않은 작업이 포트폴리오 근거로 실린다.
_EXAMPLE_MARK = "[예시]"
_GENERATED_LOG_PREFIXES = ("프로젝트 ", "포트폴리오 ")


def make_notion_til_collector(
    notion_token: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> Callable[[str], Awaitable[NotionSnapshot]]:
    """Capture a request token and return a collector for one dashboard page."""

    token = (notion_token or "").strip()

    async def collect(parent_id: str) -> NotionSnapshot:
        target = (parent_id or "").strip()
        if not token:
            return NotionSnapshot(complete=False, warnings=["Notion token is missing"])
        if not target:
            return NotionSnapshot(complete=False, warnings=["Notion TIL parent is missing"])

        live = client or httpx.AsyncClient(
            base_url=API_URL.rstrip("/"),
            timeout=60.0,
        )
        headers = {
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }
        warnings: list[str] = []
        entries: list[TilEntry] = []
        complete = True
        try:
            pages = await _child_pages(live, target, headers)
            for page_id, fallback_title in pages:
                try:
                    entry = await _read_entry(live, page_id, fallback_title, headers)
                except Exception as exc:
                    complete = False
                    warnings.append(f"Notion TIL page {page_id}: {_safe_error(exc, token)}")
                    continue
                if entry is not None:
                    entries.append(entry)
        except Exception as exc:
            complete = False
            warnings.append(f"Notion TIL collection failed: {_safe_error(exc, token)}")
        finally:
            if client is None:
                await live.aclose()

        return NotionSnapshot(entries=entries, complete=complete, warnings=warnings)

    return collect


async def collect_notion_til(
    parent_id: str,
    notion_token: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> NotionSnapshot:
    return await make_notion_til_collector(notion_token, client=client)(parent_id)


async def _child_pages(
    client: httpx.AsyncClient, parent_id: str, headers: dict[str, str]
) -> list[tuple[str, str | None]]:
    pages: list[tuple[str, str | None]] = []
    pending = [parent_id]
    seen: set[str] = set()
    while pending:
        current = pending.pop(0)
        if current in seen:
            continue
        seen.add(current)
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            payload = await _request(client, "GET", f"/blocks/{current}/children", headers, params=params)
            for item in payload.get("results", []):
                if not isinstance(item, dict):
                    continue
                item_id = str(item.get("id") or "").strip()
                if not item_id:
                    continue
                if item.get("type") == "child_page":
                    child = item.get("child_page") or {}
                    title = str(child.get("title") or "").strip()
                    if _is_generated_log(title):
                        continue
                    pages.append((item_id, title or None))
                    pending.append(item_id)
                elif item.get("has_children"):
                    pending.append(item_id)
            if not payload.get("has_more"):
                break
            cursor = str(payload.get("next_cursor") or "") or None
            if cursor is None:
                break
    return pages


async def _read_entry(
    client: httpx.AsyncClient,
    page_id: str,
    fallback_title: str | None,
    headers: dict[str, str],
) -> TilEntry | None:
    if fallback_title and (
        _EXAMPLE_MARK in fallback_title
        or _is_generated_log(fallback_title)
        or _title_parts(fallback_title, "")[0] is None
    ):
        return None
    page = await _request(client, "GET", f"/pages/{page_id}", headers)
    markdown_payload = await _request(client, "GET", f"/pages/{page_id}/markdown", headers)
    markdown = str(markdown_payload.get("markdown") or "")
    raw_title = _title_of(page) or fallback_title or page_id
    if _EXAMPLE_MARK in raw_title or _is_generated_log(raw_title):
        return None
    parsed_date, title = _title_parts(raw_title, markdown)
    if parsed_date is None:
        return None
    return TilEntry(
        date=parsed_date,
        title=title,
        body_markdown=markdown,
        page_id=page_id,
        tags=_tags_of(page),
    )


async def _request(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    headers: dict[str, str],
    **kwargs: Any,
) -> dict[str, Any]:
    response = await client.request(method, path, headers=headers, **kwargs)
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(f"notion api {exc.response.status_code} {path}") from exc
    payload = response.json()
    return payload if isinstance(payload, dict) else {"results": payload}


def _title_of(page: dict[str, Any]) -> str | None:
    properties = page.get("properties")
    if not isinstance(properties, dict):
        return None
    for value in properties.values():
        if not isinstance(value, dict):
            continue
        for key in ("title", "rich_text"):
            items = value.get(key)
            if isinstance(items, list):
                text = "".join(
                    str(item.get("plain_text") or "")
                    for item in items
                    if isinstance(item, dict)
                ).strip()
                if text:
                    return text
        if isinstance(value.get("title"), str) and value["title"].strip():
            return value["title"].strip()
    return None


def _title_parts(raw_title: str, markdown: str) -> tuple[date | None, str]:
    title = raw_title.strip()
    match = _DATE_IN_TITLE.match(title)
    if match:
        parsed = date.fromisoformat(match.group("date"))
        return parsed, (match.group("title") or title).strip()
    body_match = _DATE_IN_BODY.search(markdown)
    if body_match:
        return date.fromisoformat(body_match.group(1)), title
    return None, title


def _tags_of(page: dict[str, Any]) -> list[str]:
    properties = page.get("properties")
    if not isinstance(properties, dict):
        return []
    for key, value in properties.items():
        if str(key).casefold() not in _TAG_KEYS or not isinstance(value, dict):
            continue
        selected = value.get("multi_select")
        if isinstance(selected, list):
            return [
                str(item.get("name") or "").strip()
                for item in selected
                if isinstance(item, dict) and str(item.get("name") or "").strip()
            ]
        selected = value.get("select")
        if isinstance(selected, dict) and selected.get("name"):
            return [str(selected["name"]).strip()]
    return []


def _is_generated_log(title: str) -> bool:
    return title.startswith(_GENERATED_LOG_PREFIXES)


def _safe_error(exc: Exception, token: str) -> str:
    text = str(exc).replace(token, "«token»") if token else str(exc)
    return text[:180] or type(exc).__name__


__all__ = ["API_URL", "NOTION_VERSION", "collect_notion_til", "make_notion_til_collector"]
