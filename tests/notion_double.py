"""An in-memory Notion workspace for tests.

Models the one property that matters for the dashboard rules: a page has a
parent, and a page with no parent sits at the private root. Everything the
worker asks of a real session — create, fetch, update, search — is answered
against that tree, so a test can assert where a page landed rather than only
that a call happened.

`mcp_session` wraps the same workspace behind the tool schemas the live server
advertises. Tests that care about policy take the workspace directly; tests
that should also catch a schema mistake go through the session.
"""

from __future__ import annotations

from typing import Any

from app.publish.notion_mcp import McpSession
from app.publish.notion_template import ARCHIVE_TITLE, DASHBOARD_TITLE

LIVE_CREATE_PAGES: dict[str, Any] = {
    "type": "object",
    "properties": {
        "parent": {
            "anyOf": [
                {"type": "object", "properties": {"page_id": {"type": "string"}}},
                {"type": "object", "properties": {"data_source_id": {"type": "string"}}},
                {"type": "object", "properties": {"workspace": {"type": "boolean"}}},
            ]
        },
        "pages": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "properties": {
                        "type": "object",
                        "properties": {"title": {"type": "string"}},
                        "additionalProperties": False,
                    },
                    "content": {"type": "string"},
                },
                "required": ["properties", "content"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["pages"],
}

LIVE_FETCH: dict[str, Any] = {
    "type": "object",
    "properties": {"id": {"type": "string"}},
    "required": ["id"],
}

LIVE_UPDATE_PAGE: dict[str, Any] = {
    "type": "object",
    "properties": {
        "page_id": {"type": "string"},
        "command": {"type": "string", "enum": ["replace_content", "insert_content_after"]},
        "content": {"type": "string"},
    },
    "required": ["page_id", "command", "content"],
}

LIVE_SEARCH: dict[str, Any] = {
    "type": "object",
    "properties": {"query": {"type": "string"}},
    "required": ["query"],
}

LIVE_LIST_PRIVATE: dict[str, Any] = {
    "type": "object",
    "properties": {
        "limit": {"type": "number"},
        "cursor": {"type": "string"},
    },
}


class NotionWorkspace:
    """Records pages and answers the four tools `notion_dashboard` uses."""

    def __init__(self, *, searchable: bool = True) -> None:
        self.pages: list[dict[str, Any]] = []
        self.searchable = searchable
        self.creates: list[dict[str, Any]] = []
        self.updates: list[dict[str, Any]] = []
        self.searches: list[str] = []
        self.root_lists = 0

    # -- construction helpers -------------------------------------------------

    def seed(self, title: str, *, parent_id: str | None = None, body: str = "seed") -> str:
        page_id = f"page-{len(self.pages) + 1}"
        self.pages.append(
            {"id": page_id, "title": title, "parent_id": parent_id, "markdown": body}
        )
        return page_id

    def seed_dashboard(self) -> str:
        return self.seed(DASHBOARD_TITLE)

    def titles_under(self, parent_id: str) -> list[str]:
        return [page["title"] for page in self.pages if page["parent_id"] == parent_id]

    @property
    def dashboard_id(self) -> str | None:
        return next(
            (
                page["id"]
                for page in self.pages
                if page["title"] == DASHBOARD_TITLE and page["parent_id"] is None
            ),
            None,
        )

    @property
    def archive_id(self) -> str | None:
        dashboard = self.dashboard_id
        return next(
            (
                page["id"]
                for page in self.pages
                if page["title"] == ARCHIVE_TITLE and page["parent_id"] == dashboard
            ),
            None,
        )

    @property
    def logs(self) -> list[dict[str, Any]]:
        """What the agent generated, oldest first.

        Under the archive page, not the dashboard: the dashboard only ever
        holds template pages. Empty until the first publish creates it — never
        the root pages, which is what matching a `None` parent would give.
        """
        archive = self.archive_id
        return [page for page in self.pages if archive and page["parent_id"] == archive]

    def body_of(self, page_id: str) -> str | None:
        page = self._get(page_id)
        return page["markdown"] if page else None

    # -- the NotionSession protocol -------------------------------------------

    async def create_page(
        self, *, title: str, markdown: str, parent_id: str | None, icon: str | None = None
    ) -> tuple[str | None, str | None]:
        self.creates.append({"title": title, "parent_id": parent_id, "icon": icon})
        page_id = self.seed(title, parent_id=parent_id, body=markdown)
        return page_id, f"https://notion.so/{page_id}"

    async def read_page(self, target: str) -> Any:
        page = self._get(target)
        if page is None:
            raise RuntimeError(f"page not found: {target}")
        mentions = "".join(
            f'<page url="https://notion.so/{child["id"]}">{child["title"]}</page>\n'
            for child in self.pages
            if child["parent_id"] == page["id"]
        )
        return {
            "page_url": f"https://notion.so/{page['id']}",
            "title": page["title"],
            "text": mentions + page["markdown"],
            "metadata": {"id": page["id"], "type": "page"},
        }

    async def list_root_pages(self) -> list[dict[str, Any]]:
        self.root_lists += 1
        return [
            {
                "id": page["id"],
                "title": page["title"],
                "url": f"https://notion.so/{page['id']}",
                "parent_id": None,
            }
            for page in self.pages
            if page["parent_id"] is None
        ]

    async def update_page(
        self, page_id: str, markdown: str, title: str | None = None
    ) -> tuple[str | None, str | None]:
        page = self._get(page_id)
        if page is None:
            raise RuntimeError(f"page not found: {page_id}")
        page["markdown"] = markdown
        self.updates.append({"page_id": page_id, "markdown": markdown})
        return page_id, f"https://notion.so/{page_id}"

    async def search_pages(self, query: str) -> list[dict[str, Any]]:
        self.searches.append(query)
        if not self.searchable:
            raise RuntimeError("search is not available on this plan")
        needle = query.strip().lower()
        return [
            {
                "id": page["id"],
                "title": page["title"],
                "url": f"https://notion.so/{page['id']}",
                "parent_id": page["parent_id"],
            }
            for page in self.pages
            if needle in page["title"].lower()
        ]

    def _get(self, identifier: str) -> dict[str, Any] | None:
        return next((page for page in self.pages if page["id"] == identifier), None)


class _Tool:
    """One MCP tool: an advertised schema and a coroutine to run it."""

    def __init__(self, schema: dict[str, Any], run: Any) -> None:
        self.args_schema = schema
        self._run = run

    async def ainvoke(self, call: dict[str, Any]) -> Any:
        return await self._run(call["args"])


def mcp_session(workspace: NotionWorkspace, *, searchable: bool = True) -> McpSession:
    """The workspace behind the live tool schemas, so argument shape is tested too."""

    async def create(args: dict[str, Any]) -> Any:
        page = args["pages"][0]
        page_id, url = await workspace.create_page(
            title=page["properties"]["title"],
            markdown=page["content"],
            parent_id=(args.get("parent") or {}).get("page_id"),
        )
        return {"pages": [{"id": page_id, "url": url}]}

    async def fetch(args: dict[str, Any]) -> Any:
        return await workspace.read_page(args["id"])

    async def update(args: dict[str, Any]) -> Any:
        assert args["command"] == "replace_content", args["command"]
        page_id, url = await workspace.update_page(args["page_id"], args["content"])
        return {"page": {"id": page_id, "url": url}}

    async def search(args: dict[str, Any]) -> Any:
        hits = await workspace.search_pages(args["query"])
        return {
            "results": [
                {
                    "id": hit["id"],
                    "title": hit["title"],
                    "url": hit["url"],
                    "parent": {"page_id": hit["parent_id"]} if hit["parent_id"] else None,
                }
                for hit in hits
            ]
        }

    async def list_root(args: dict[str, Any]) -> Any:
        return {"results": await workspace.list_root_pages()}

    tools = {
        "notion-create-pages": _Tool(LIVE_CREATE_PAGES, create),
        "notion-fetch": _Tool(LIVE_FETCH, fetch),
        "notion-update-page": _Tool(LIVE_UPDATE_PAGE, update),
        "notion-list-private-pages": _Tool(LIVE_LIST_PRIVATE, list_root),
    }
    if searchable:
        tools["notion-search"] = _Tool(LIVE_SEARCH, search)
    return McpSession(tools=tools)
