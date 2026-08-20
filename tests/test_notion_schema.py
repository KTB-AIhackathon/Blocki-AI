"""The schemas and responses here are the ones the live Notion MCP server used.

Sources: `tests/test_notion_mcp.py` and `docs/notion-verification/*.json` on the
`feat/notion-mcp-collector` branch. If Notion changes the schema again, these
are the fixtures to update.
"""

from __future__ import annotations

import json

import pytest

from app.publish.notion_schema import (
    SchemaMismatch,
    children_from,
    create_page_arguments,
    hits_from,
    page_from,
    page_id_from_url,
    search_arguments,
    title_of,
    update_page_arguments,
)
from tests.notion_double import LIVE_CREATE_PAGES, LIVE_SEARCH, LIVE_UPDATE_PAGE

FLAT_STUB = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "markdown": {"type": "string"},
        "parent_id": {"type": "string"},
    },
    "required": ["title", "markdown"],
}


def test_the_live_schema_is_filled_the_way_notion_expects() -> None:
    args = create_page_arguments(
        LIVE_CREATE_PAGES, title="포트폴리오 2026-08-20", markdown="# 본문\n", parent_id="parent-1"
    )

    assert args == {
        "parent": {"page_id": "parent-1"},
        "pages": [{"properties": {"title": "포트폴리오 2026-08-20"}, "content": "# 본문\n"}],
    }


def test_no_parent_id_means_no_parent_key_when_it_is_optional() -> None:
    args = create_page_arguments(LIVE_CREATE_PAGES, title="T", markdown="B", parent_id=None)
    assert "parent" not in args


def test_a_required_parent_falls_back_to_the_workspace_root() -> None:
    schema = {**LIVE_CREATE_PAGES, "required": ["pages", "parent"]}
    args = create_page_arguments(schema, title="T", markdown="B", parent_id=None)
    assert args["parent"] == {"workspace": True}


def test_a_database_parent_is_used_when_pages_are_not_offered() -> None:
    schema = {
        "type": "object",
        "properties": {
            "parent": {"type": "object", "properties": {"data_source_id": {"type": "string"}}},
            "pages": LIVE_CREATE_PAGES["properties"]["pages"],
        },
        "required": ["pages"],
    }
    args = create_page_arguments(schema, title="T", markdown="B", parent_id="ds-1")
    assert args["parent"] == {"data_source_id": "ds-1"}


def test_a_flat_schema_is_still_supported() -> None:
    args = create_page_arguments(FLAT_STUB, title="T", markdown="B", parent_id="p1")
    assert args == {"title": "T", "markdown": "B", "parent_id": "p1"}


def test_a_flat_schema_omits_the_parent_rather_than_sending_null() -> None:
    args = create_page_arguments(FLAT_STUB, title="T", markdown="B", parent_id=None)
    assert args == {"title": "T", "markdown": "B"}


def test_a_parent_we_cannot_express_is_refused_rather_than_misfiled() -> None:
    """Writing to the workspace root when a parent was named is a silent data loss."""
    schema = {
        "type": "object",
        "properties": {"pages": LIVE_CREATE_PAGES["properties"]["pages"]},
        "required": ["pages"],
    }
    with pytest.raises(SchemaMismatch):
        create_page_arguments(schema, title="T", markdown="B", parent_id="parent-1")


def test_an_unfillable_required_field_is_refused() -> None:
    schema = {
        "type": "object",
        "properties": {
            "pages": LIVE_CREATE_PAGES["properties"]["pages"],
            "icon": {"type": "string"},
        },
        "required": ["pages", "icon"],
    }
    with pytest.raises(SchemaMismatch):
        create_page_arguments(schema, title="T", markdown="B")


def test_a_title_the_schema_forbids_is_refused() -> None:
    schema = json.loads(json.dumps(LIVE_CREATE_PAGES))
    schema["properties"]["pages"]["items"]["properties"]["properties"]["properties"] = {}
    with pytest.raises(SchemaMismatch):
        create_page_arguments(schema, title="T", markdown="B")


def test_an_empty_schema_is_refused() -> None:
    with pytest.raises(SchemaMismatch):
        create_page_arguments(None, title="T", markdown="B")


def test_the_live_result_shape_is_read() -> None:
    assert page_from({"pages": [{"id": "page-1", "url": "https://notion.so/page-1"}]}) == (
        "page-1",
        "https://notion.so/page-1",
    )


def test_structured_content_is_preferred() -> None:
    raw = [
        {"structuredContent": {"pages": [{"id": "real", "url": "https://notion.so/real"}]}},
        [{"type": "text", "id": "lc_block", "text": "created"}],
    ]
    assert page_from(raw) == ("real", "https://notion.so/real")


def test_json_hidden_in_a_text_block_is_unwrapped() -> None:
    """The transport's own block id must never be mistaken for the page id."""
    raw = [
        {
            "type": "text",
            "id": "lc_transport_id",
            "text": json.dumps(
                {
                    "created_page_id": "3c10ffe9-306d-8106-a9d6-f65855403b39",
                    "created_page_url": "https://app.notion.com/p/3c10ffe9306d8106a9d6f65855403b39",
                }
            ),
        }
    ]
    assert page_from(raw) == (
        "3c10ffe9-306d-8106-a9d6-f65855403b39",
        "https://app.notion.com/p/3c10ffe9306d8106a9d6f65855403b39",
    )


def test_an_id_without_a_url_is_accepted_on_the_second_pass() -> None:
    assert page_from({"page_id": "page-1"}) == ("page-1", None)


def test_a_pair_beats_a_lone_id_found_earlier() -> None:
    raw = {"meta": {"id": "not-the-page"}, "pages": [{"id": "page-1", "url": "u"}]}
    assert page_from(raw) == ("page-1", "u")


def test_a_bare_error_string_is_not_mistaken_for_a_page() -> None:
    assert page_from([{"type": "text", "text": "Unauthorized"}]) == (None, None)


def test_nothing_recognisable_yields_nothing() -> None:
    assert page_from({}) == (None, None)
    assert page_from(None) == (None, None)


# --------------------------------------------------------------------------
# icon, update, search: the arguments the dashboard work needs
# --------------------------------------------------------------------------


def test_an_icon_is_sent_only_when_the_server_offers_a_field_for_it() -> None:
    """The live create schema forbids extra page fields, so the icon is dropped."""
    args = create_page_arguments(LIVE_CREATE_PAGES, title="T", markdown="B", icon="🧑‍💻")
    assert args["pages"][0] == {"properties": {"title": "T"}, "content": "B"}


def test_an_icon_is_filled_when_the_schema_advertises_it() -> None:
    schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "content": {"type": "string"},
            "icon": {"type": "string"},
        },
        "required": ["title", "content"],
    }
    args = create_page_arguments(schema, title="T", markdown="B", icon="🧑‍💻")
    assert args["icon"] == "🧑‍💻"


def test_the_update_call_asks_to_replace_the_body_not_append_to_it() -> None:
    args = update_page_arguments(LIVE_UPDATE_PAGE, page_id="page-1", markdown="# 새 본문")
    assert args == {"page_id": "page-1", "command": "replace_content", "content": "# 새 본문"}


def test_an_update_schema_with_no_replace_command_is_refused() -> None:
    schema = {
        "type": "object",
        "properties": {
            "page_id": {"type": "string"},
            "command": {"type": "string", "enum": ["insert_content_after"]},
            "content": {"type": "string"},
        },
        "required": ["page_id", "command", "content"],
    }
    with pytest.raises(SchemaMismatch):
        update_page_arguments(schema, page_id="page-1", markdown="B")


def test_search_uses_the_advertised_query_field() -> None:
    assert search_arguments(LIVE_SEARCH, query="Developer TIL Dashboard") == {
        "query": "Developer TIL Dashboard"
    }


def test_a_root_hit_reports_no_parent_so_it_can_be_told_from_a_nested_one() -> None:
    raw = {
        "results": [
            {"id": "p1", "title": "Developer TIL Dashboard", "url": "u1", "parent": None},
            {
                "id": "p2",
                "title": "Developer TIL Dashboard",
                "url": "u2",
                "parent": {"page_id": "team-wiki"},
            },
        ]
    }
    assert hits_from(raw) == [
        {"id": "p1", "title": "Developer TIL Dashboard", "url": "u1", "parent_id": None},
        {"id": "p2", "title": "Developer TIL Dashboard", "url": "u2", "parent_id": "team-wiki"},
    ]


def test_search_results_wrapped_in_a_text_block_are_still_read() -> None:
    payload = json.dumps({"results": [{"id": "p1", "title": "T", "url": "u", "parent": None}]})
    assert hits_from([{"type": "text", "text": payload}]) == [
        {"id": "p1", "title": "T", "url": "u", "parent_id": None}
    ]


def test_a_private_root_list_hit_without_an_id_uses_the_url() -> None:
    raw = {
        "results": [
            {
                "type": "page",
                "title": "Developer TIL Dashboard",
                "url": "https://app.notion.com/p/6f816da5521c83ed8d89012fa47f4035",
            }
        ]
    }
    assert hits_from(raw) == [
        {
            "id": "6f816da5-521c-83ed-8d89-012fa47f4035",
            "title": "Developer TIL Dashboard",
            "url": "https://app.notion.com/p/6f816da5521c83ed8d89012fa47f4035",
            "parent_id": None,
        }
    ]


def test_child_page_mentions_are_read_from_the_parent_body() -> None:
    fetched = {
        "title": "Developer TIL Dashboard",
        "text": (
            '<page url="https://app.notion.com/p/91116da5521c82e69cef81211aad24d9">'
            "생성된 포트폴리오 및 이력서</page>\n"
            '<page url="https://notion.so/page-1">포트폴리오 2026-08-20</page>'
        ),
    }
    assert children_from(fetched) == [
        {
            "id": "91116da5-521c-82e6-9cef-81211aad24d9",
            "title": "생성된 포트폴리오 및 이력서",
            "url": "https://app.notion.com/p/91116da5521c82e69cef81211aad24d9",
            "parent_id": None,
        },
        {
            "id": "page-1",
            "title": "포트폴리오 2026-08-20",
            "url": "https://notion.so/page-1",
            "parent_id": None,
        },
    ]


def test_a_notion_site_slug_still_yields_the_page_id() -> None:
    assert page_id_from_url(
        "https://sedate-faucet-61e.notion.site/Developer-TIL-Dashboard-6f816da5521c83ed8d89012fa47f4035"
    ) == "6f816da5-521c-83ed-8d89-012fa47f4035"


def test_the_title_is_read_past_the_page_body() -> None:
    """A fetch carries both; picking `text` first would return the markdown."""
    fetched = {
        "page_url": "https://notion.so/p1",
        "title": "Developer TIL Dashboard",
        "text": "## 🗓️ 주간 TIL 목록",
        "metadata": {"id": "p1", "type": "page"},
    }
    assert title_of(fetched) == "Developer TIL Dashboard"
