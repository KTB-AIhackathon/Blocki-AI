"""Talk to Notion's hosted MCP server with a token Spring handed us.

The prototype on `feat/notion-mcp-collector` ran its own OAuth dance and wrote
the result to a file under `work/`. That vault does not belong here: this
worker is stateless and Spring owns user credentials. Everything else from that
prototype — the tool names, the runtime schema adaptation, the result shapes —
was verified against the live server and is kept.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from app.publish.notion_schema import (
    create_page_arguments,
    hits_from,
    page_from,
    search_arguments,
    update_page_arguments,
)

CREATE_TOOLS = ("notion-create-pages", "create_page", "create-pages", "API-post-page")
FETCH_TOOLS = ("notion-fetch", "fetch", "get_page", "API-retrieve-a-page")
UPDATE_TOOLS = ("notion-update-page", "update_page", "update-page", "API-patch-page")
LIST_ROOT_TOOLS = (
    "notion-list-private-pages",
    "list_private_pages",
    "list-private-pages",
)
SEARCH_TOOLS = ("notion-search", "search", "API-post-search")
FETCH_ARG_KEYS = ("id", "url", "page_id", "page_url")


class ToolUnavailable(RuntimeError):
    """The server does not offer a tool we need. Distinct from the call failing."""


class NotionSession(Protocol):
    async def create_page(
        self, *, title: str, markdown: str, parent_id: str | None, icon: str | None = None
    ) -> tuple[str | None, str | None]: ...

    async def read_page(self, target: str) -> Any: ...

    async def update_page(
        self, page_id: str, markdown: str, title: str | None = None
    ) -> tuple[str | None, str | None]: ...

    async def list_root_pages(self) -> list[dict[str, Any]]: ...

    async def search_pages(self, query: str) -> list[dict[str, Any]]: ...


def notion_mcp_url() -> str:
    return os.environ.get("NOTION_MCP_URL", "https://mcp.notion.com/mcp")


@dataclass(frozen=True)
class McpSession:
    tools: dict[str, Any]

    async def create_page(
        self, *, title: str, markdown: str, parent_id: str | None, icon: str | None = None
    ) -> tuple[str | None, str | None]:
        name, tool = self._pick(CREATE_TOOLS, "create")
        arguments = create_page_arguments(
            getattr(tool, "args_schema", None),
            title=title,
            markdown=markdown,
            parent_id=parent_id,
            icon=icon,
        )
        return page_from(await _invoke(tool, name, arguments))

    async def read_page(self, target: str) -> Any:
        name, tool = self._pick(FETCH_TOOLS, "fetch")
        properties = getattr(tool, "args_schema", None) or {}
        properties = properties.get("properties") if isinstance(properties, dict) else {}
        key = next(
            (k for k in FETCH_ARG_KEYS if isinstance(properties, dict) and k in properties),
            "id",
        )
        return await _invoke(tool, name, {key: target})

    async def update_page(
        self, page_id: str, markdown: str, title: str | None = None
    ) -> tuple[str | None, str | None]:
        name, tool = self._pick(UPDATE_TOOLS, "update")
        arguments = update_page_arguments(
            getattr(tool, "args_schema", None), page_id=page_id, markdown=markdown
        )
        page, url = page_from(await _invoke(tool, name, arguments))
        # An update that reports nothing still updated the page we named.
        return page or page_id, url

    async def list_root_pages(self) -> list[dict[str, Any]]:
        name, tool = self._pick(LIST_ROOT_TOOLS, "list-root")
        properties = getattr(tool, "args_schema", None) or {}
        properties = properties.get("properties") if isinstance(properties, dict) else {}
        arguments: dict[str, Any] = {}
        if isinstance(properties, dict) and "limit" in properties:
            arguments["limit"] = 100
        return hits_from(await _invoke(tool, name, arguments))

    async def search_pages(self, query: str) -> list[dict[str, Any]]:
        name, tool = self._pick(SEARCH_TOOLS, "search")
        arguments = search_arguments(getattr(tool, "args_schema", None), query=query)
        return hits_from(await _invoke(tool, name, arguments))

    def _pick(self, names: tuple[str, ...], role: str) -> tuple[str, Any]:
        for name in names:
            if name in self.tools:
                return name, self.tools[name]
        raise ToolUnavailable(
            f"notion {role} tool missing; server offers {sorted(self.tools)[:12]}"
        )


async def open_session(notion_token: str) -> NotionSession:
    from app.publish.notion_rest import is_integration_token, open_rest_session

    if is_integration_token(notion_token):
        return await open_rest_session(notion_token)

    from langchain_mcp_adapters.client import MultiServerMCPClient

    client = MultiServerMCPClient(
        {
            "notion": {
                "transport": "http",
                "url": notion_mcp_url(),
                "headers": {
                    "Authorization": f"Bearer {notion_token}",
                    "Notion-Version": os.environ.get("NOTION_VERSION", "2022-06-28"),
                },
            }
        },
        handle_tool_errors=False,
    )
    return McpSession(tools={tool.name: tool for tool in await client.get_tools()})


async def _invoke(tool: Any, name: str, arguments: dict[str, Any]) -> Any:
    """Ask for the tool_call form so `structuredContent` survives, not just text."""
    result = await tool.ainvoke(
        {"name": name, "args": arguments, "id": str(uuid.uuid4()), "type": "tool_call"}
    )
    artifact = getattr(result, "artifact", None)
    content = getattr(result, "content", result)
    return [artifact, content] if artifact is not None else content
