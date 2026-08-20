"""Shape the arguments and read the answer for Notion's create-page tool.

Notion hosts its MCP server; we do not own the tool schema and it has changed
before. So nothing here hardcodes an argument layout. Both functions take what
the server advertised at runtime and adapt to it.

The shapes handled are the ones observed against the live server
(`docs/notion-verification/` on `feat/notion-mcp-collector`):

    notion-create-pages  {"parent": {"page_id": ...},
                          "pages": [{"properties": {"title": ...},
                                     "content": "<markdown>"}]}
    -> {"pages": [{"id": ..., "url": ...}]}

Markdown survives that round trip intact: fenced code, pipe tables, task
checkboxes, heading levels and emoji all came back unchanged.

Pure functions, no I/O, so the adaptation is testable without a Notion session.
"""

from __future__ import annotations

import json
import re
from typing import Any

_PAGE_TAG = re.compile(r'<page\s+url="([^"]+)"[^>]*>(.*?)</page>', re.IGNORECASE | re.DOTALL)
_HEX32 = re.compile(r"([0-9a-fA-F]{32})$")
_UUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

CONTENT_KEYS = ("content", "markdown", "body_markdown", "body")
TITLE_KEYS = ("title", "name")
PARENT_ID_KEYS = ("parent_id", "parent_page_id", "page_id")
ICON_KEYS = ("icon", "emoji", "icon_emoji")
QUERY_KEYS = ("query", "q", "search", "text")
TARGET_KEYS = ("page_id", "data", "id", "page", "page_url", "url")

ID_KEYS = ("page_id", "created_page_id", "id")
URL_KEYS = ("page_url", "created_page_url", "url")


class SchemaMismatch(RuntimeError):
    """The advertised schema has no layout we know how to fill."""


def create_page_arguments(
    schema: Any,
    *,
    title: str,
    markdown: str,
    parent_id: str | None = None,
    icon: str | None = None,
) -> dict[str, Any]:
    """Fill `schema` with one page. Raises SchemaMismatch if it cannot be filled."""
    properties = _properties(schema)
    if not properties:
        raise SchemaMismatch("root properties are missing")

    pages = properties.get("pages")
    if isinstance(pages, dict) and pages.get("type") == "array":
        return _nested(schema, properties, pages, title, markdown, parent_id, icon)
    return _flat(schema, properties, title, markdown, parent_id, icon)


def update_page_arguments(schema: Any, *, page_id: str, markdown: str) -> dict[str, Any]:
    """Replace a page's body. Which argument names that takes is the server's call."""
    properties = _properties(schema)
    if not properties:
        raise SchemaMismatch("update schema has no properties")

    target = _first(properties, TARGET_KEYS)
    content = _first(properties, CONTENT_KEYS)
    if target is None or content is None:
        raise SchemaMismatch("update schema has no page target and content fields")

    arguments: dict[str, Any] = {target: page_id, content: markdown}
    # Notion's own update tool takes a command telling it what to do with the
    # content; without it the call is ambiguous and the body is not replaced.
    command = properties.get("command")
    if isinstance(command, dict):
        choice = _replace_choice(command)
        if choice is None:
            raise SchemaMismatch("update schema offers no whole-body replace command")
        arguments["command"] = choice

    unmet = set(_required(schema)) - set(arguments)
    if unmet:
        raise SchemaMismatch(f"update requires fields we cannot fill: {sorted(unmet)}")
    return arguments


def search_arguments(schema: Any, *, query: str) -> dict[str, Any]:
    properties = _properties(schema)
    key = _first(properties, QUERY_KEYS)
    if key is None:
        raise SchemaMismatch("search schema has no query field")
    arguments: dict[str, Any] = {key: query}
    unmet = set(_required(schema)) - set(arguments)
    if unmet:
        raise SchemaMismatch(f"search requires fields we cannot fill: {sorted(unmet)}")
    return arguments


def hits_from(raw: Any) -> list[dict[str, Any]]:
    """Flatten a search result into `{id, title, url, parent_id}` dicts.

    Tolerant on purpose: search results carry more shape variation than create
    results, and a hit we fail to parse only costs us a duplicate check.
    """
    hits: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in _candidates(raw):
        _collect_hits(value, hits, seen, depth=0)
    return hits


def title_of(raw: Any) -> str | None:
    """The title a fetch returned, wherever the server chose to put it."""
    for value in _candidates(raw):
        found = _find_title(value, depth=0)
        if found:
            return found
    return None


def page_id_from_url(url: str | None) -> str | None:
    """A page id hidden in a Notion URL, or the last path segment."""
    if not url or not str(url).strip():
        return None
    slug = str(url).strip().split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
    if _UUID.match(slug):
        return slug.lower()
    hex_id = _HEX32.search(slug)
    if hex_id is None:
        return slug or None
    h = hex_id.group(1).lower()
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def children_from(raw: Any) -> list[dict[str, Any]]:
    """Child page mentions inside a fetched dashboard body.

    The live fetch format is `<page url="...">title</page>`. We never search
    the rest of the workspace for these; the parent page is the only source.
    """
    children: list[dict[str, Any]] = []
    seen: set[str] = set()
    for blob in _text_blobs(raw):
        for url, title in _PAGE_TAG.findall(blob):
            page_id = page_id_from_url(url)
            cleaned = _clean_title(title)
            if not page_id or not cleaned or page_id in seen:
                continue
            seen.add(page_id)
            children.append(
                {"id": page_id, "title": cleaned, "url": url, "parent_id": None}
            )
    return children


def _clean_title(title: str) -> str:
    return title.replace("\\[", "[").replace("\\]", "]").strip()


def _text_blobs(raw: Any) -> list[str]:
    blobs: list[str] = []
    for value in _candidates(raw):
        if isinstance(value, str):
            blobs.append(value)
        elif isinstance(value, dict) and isinstance(value.get("text"), str):
            blobs.append(value["text"])
    if isinstance(raw, str):
        blobs.append(raw)
    return blobs


def _replace_choice(command: dict[str, Any]) -> str | None:
    choices = command.get("enum")
    if not isinstance(choices, list):
        return "replace_content"
    text = [str(choice) for choice in choices]
    for wanted in ("replace_content", "replace", "update_content", "overwrite"):
        if wanted in text:
            return wanted
    return next((choice for choice in text if "replace" in choice), None)


def page_from(raw: Any) -> tuple[str | None, str | None]:
    """Dig the created page's id and url out of whatever the tool returned."""
    for value in _candidates(raw):
        found = _visit(value, strict=True)
        if found:
            return found
    for value in _candidates(raw):
        found = _visit(value, strict=False)
        if found:
            return found
    return None, None


def _nested(
    schema: dict[str, Any],
    properties: dict[str, Any],
    pages: dict[str, Any],
    title: str,
    markdown: str,
    parent_id: str | None,
    icon: str | None = None,
) -> dict[str, Any]:
    item = pages.get("items")
    item_properties = _properties(item)
    if not item_properties:
        raise SchemaMismatch("pages.items.properties are missing")

    page: dict[str, Any] = {}
    nested_title = item_properties.get("properties")
    if isinstance(nested_title, dict):
        allowed = _properties(nested_title)
        if nested_title.get("additionalProperties") is False and "title" not in allowed:
            raise SchemaMismatch("pages.items.properties.title is missing")
        page["properties"] = {"title": title}
        _set_icon(allowed, page["properties"], nested_title, icon)
    else:
        key = _first(item_properties, TITLE_KEYS)
        if key is None:
            raise SchemaMismatch("pages.items has no title field")
        page[key] = title

    content = _first(item_properties, CONTENT_KEYS)
    if content is None:
        raise SchemaMismatch("pages.items has no content field")
    page[content] = markdown
    _set_icon(item_properties, page, item, icon)

    required = set(_required(item))
    unmet = required - set(page)
    if unmet:
        raise SchemaMismatch(f"pages.items requires fields we cannot fill: {sorted(unmet)}")

    arguments: dict[str, Any] = {"pages": [page]}
    parent = _parent(properties.get("parent"), parent_id, required="parent" in _required(schema))
    if parent is not None:
        arguments["parent"] = parent
    elif parent_id:
        raise SchemaMismatch("parent cannot be set, the page would land in the wrong place")

    unmet = set(_required(schema)) - set(arguments)
    if unmet:
        raise SchemaMismatch(f"root requires fields we cannot fill: {sorted(unmet)}")
    return arguments


def _flat(
    schema: dict[str, Any],
    properties: dict[str, Any],
    title: str,
    markdown: str,
    parent_id: str | None,
    icon: str | None = None,
) -> dict[str, Any]:
    title_key = _first(properties, TITLE_KEYS)
    content_key = _first(properties, CONTENT_KEYS)
    if title_key is None or content_key is None:
        raise SchemaMismatch("no supported title and content fields found")

    arguments: dict[str, Any] = {title_key: title, content_key: markdown}
    _set_icon(properties, arguments, schema, icon)

    parent_key = _first(properties, PARENT_ID_KEYS)
    if parent_key is not None:
        # Sending an explicit null is not the same as omitting the field; some
        # servers reject it.
        if parent_id:
            arguments[parent_key] = parent_id
    else:
        parent = _parent(
            properties.get("parent"), parent_id, required="parent" in _required(schema)
        )
        if parent is not None:
            arguments["parent"] = parent
        elif parent_id:
            raise SchemaMismatch("parent cannot be set, the page would land in the wrong place")

    unmet = set(_required(schema)) - set(arguments)
    if unmet:
        raise SchemaMismatch(f"root requires fields we cannot fill: {sorted(unmet)}")
    return arguments


def _set_icon(
    properties: dict[str, Any], target: dict[str, Any], owner: Any, icon: str | None
) -> None:
    """Best effort. The icon is decoration; the title is what we search on.

    A server that does not advertise an icon field gets a page without one
    rather than a rejected call.
    """
    if not icon:
        return
    key = _first(properties, ICON_KEYS)
    if key is None:
        return
    if isinstance(owner, dict) and owner.get("additionalProperties") is False:
        if key not in properties:
            return
    target[key] = icon


def _collect_hits(
    value: Any, hits: list[dict[str, Any]], seen: set[str], *, depth: int
) -> None:
    if depth > 8:
        return
    if isinstance(value, (list, tuple)):
        for child in value:
            _collect_hits(child, hits, seen, depth=depth + 1)
        return
    if not isinstance(value, dict):
        return

    text = value.get("text")
    if isinstance(text, str):
        parsed = _loads(text)
        if parsed is not None:
            _collect_hits(parsed, hits, seen, depth=depth + 1)
        return

    identifier = _pick(value, ID_KEYS) or page_id_from_url(
        _text_or_none(_pick(value, URL_KEYS))
    )
    title = _pick(value, TITLE_KEYS)
    if identifier and title:
        key = str(identifier)
        if key not in seen:
            seen.add(key)
            hits.append(
                {
                    "id": key,
                    "title": str(title),
                    "url": _text_or_none(_pick(value, URL_KEYS)),
                    "parent_id": _parent_id_of(value),
                }
            )
    for child in value.values():
        _collect_hits(child, hits, seen, depth=depth + 1)


def _parent_id_of(value: dict[str, Any]) -> str | None:
    """`None` also means "the private root": Notion omits a parent page there."""
    parent = value.get("parent")
    if isinstance(parent, str):
        return parent
    if isinstance(parent, dict):
        for key in ("page_id", "database_id", "data_source_id", "id"):
            found = parent.get(key)
            if isinstance(found, str) and found.strip():
                return found
    return _text_or_none(value.get("parent_id"))


def _find_title(value: Any, *, depth: int) -> str | None:
    if depth > 8:
        return None
    if isinstance(value, (list, tuple)):
        for child in value:
            found = _find_title(child, depth=depth + 1)
            if found:
                return found
        return None
    if not isinstance(value, dict):
        return None
    # A fetch result carries both the title and the page body under `text`, so
    # the title is checked first; only a bare content block gets unwrapped.
    title = _pick(value, TITLE_KEYS)
    if isinstance(title, str) and title.strip():
        return title
    text = value.get("text")
    if isinstance(text, str):
        parsed = _loads(text)
        return _find_title(parsed, depth=depth + 1) if parsed is not None else None
    for child in value.values():
        found = _find_title(child, depth=depth + 1)
        if found:
            return found
    return None


def _text_or_none(value: Any) -> str | None:
    return str(value) if isinstance(value, (str, int)) and str(value).strip() else None


def _parent(schema: Any, parent_id: str | None, *, required: bool) -> dict[str, Any] | None:
    """Notion accepts a page, a data source or the workspace root as the parent."""
    keys = _branch_keys(schema)
    if not keys:
        return None
    if parent_id:
        for key in ("page_id", "data_source_id", "database_id"):
            if key in keys:
                return {key: parent_id}
        return None
    if required and "workspace" in keys:
        return {"workspace": True}
    return None


def _branch_keys(schema: Any) -> set[str]:
    """Collect property names across `anyOf`/`oneOf` variants of one field."""
    keys: set[str] = set()
    stack = [schema]
    seen = 0
    while stack and seen < 64:
        node = stack.pop()
        seen += 1
        if not isinstance(node, dict):
            continue
        keys.update(_properties(node))
        for branch in ("anyOf", "oneOf", "allOf"):
            variants = node.get(branch)
            if isinstance(variants, list):
                stack.extend(variants)
    return keys


def _properties(schema: Any) -> dict[str, Any]:
    if not isinstance(schema, dict):
        return {}
    properties = schema.get("properties")
    return properties if isinstance(properties, dict) else {}


def _required(schema: Any) -> list[str]:
    if not isinstance(schema, dict):
        return []
    required = schema.get("required")
    return [str(x) for x in required] if isinstance(required, list) else []


def _first(properties: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    return next((key for key in keys if key in properties), None)


def _candidates(raw: Any) -> list[Any]:
    """Unwrap the layers a tool result can hide the payload under."""
    values: list[Any] = []
    if isinstance(raw, dict):
        for key in ("structuredContent", "structured_content", "result", "data"):
            if key in raw:
                values.append(raw[key])
        values.append(raw)
    elif isinstance(raw, (list, tuple)):
        for item in raw:
            values.extend(_candidates(item))
    elif isinstance(raw, str):
        parsed = _loads(raw)
        if parsed is not None:
            values.append(parsed)
    return values


def _visit(value: Any, *, strict: bool) -> tuple[str, str | None] | None:
    if isinstance(value, dict):
        # A content block hides the payload in `text`; the block's own `id`
        # belongs to the transport, not to the Notion page.
        text = value.get("text")
        if isinstance(text, str):
            parsed = _loads(text)
            return _visit(parsed, strict=strict) if parsed is not None else None
        page_id = _pick(value, ID_KEYS)
        page_url = _pick(value, URL_KEYS)
        if page_id and (page_url or not strict):
            return str(page_id), (str(page_url) if page_url else None)
        for child in value.values():
            found = _visit(child, strict=strict)
            if found:
                return found
    elif isinstance(value, (list, tuple)):
        for child in value:
            found = _visit(child, strict=strict)
            if found:
                return found
    return None


def _pick(value: dict[str, Any], keys: tuple[str, ...]) -> Any:
    return next((value[key] for key in keys if isinstance(value.get(key), (str, int))), None)


def _loads(text: str) -> Any:
    stripped = text.strip()
    if not stripped.startswith(("{", "[")):
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None
