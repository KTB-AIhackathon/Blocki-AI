"""Official Notion MCP transport with a user-scoped OAuth provider."""

import asyncio
import fcntl
import json
import os
import tempfile
import threading
from contextlib import asynccontextmanager, contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict
from urllib.parse import parse_qs, urlparse

import httpx2
from mcp import ClientSession
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import (
    AuthorizationCodeResult,
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthToken,
)


MCP_SERVER_URL = "https://mcp.notion.com/mcp"
# Notion advertises the resource as the full /mcp URL; a bare origin fails resource validation.
MCP_OAUTH_SERVER_URL = MCP_SERVER_URL
DEFAULT_TOKEN_PATH = Path("work/notion-mcp-tokens.json")
DEFAULT_TARGET_PATH = Path("work/notion-target.json")
TARGET_PATH = DEFAULT_TARGET_PATH
_TARGET_LOCK = threading.Lock()
AuthForUser = Callable[[str], Awaitable[httpx2.Auth] | httpx2.Auth]


class FileTokenStorage(TokenStorage):
    def __init__(self, path: Path = DEFAULT_TOKEN_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text())

    def _write(self, data: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            self.path,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            0o600,
        )
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w") as file:
                json.dump(data, file, ensure_ascii=False, indent=2)
                file.write("\n")
            descriptor = -1
        finally:
            if descriptor != -1:
                os.close(descriptor)

    async def get_tokens(self) -> OAuthToken | None:
        data = self._read().get("tokens")
        return OAuthToken.model_validate(data) if data else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        data = self._read()
        data["tokens"] = tokens.model_dump(mode="json")
        self._write(data)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        data = self._read().get("client_info")
        return OAuthClientInformationFull.model_validate(data) if data else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        data = self._read()
        data["client_info"] = client_info.model_dump(mode="json")
        self._write(data)


def _read_target_data() -> Dict[str, Any]:
    if not TARGET_PATH.exists():
        return {}
    try:
        data = json.loads(TARGET_PATH.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _target_data() -> Dict[str, Any]:
    with _target_store_lock():
        return _read_target_data()


@contextmanager
def _target_store_lock():
    with _TARGET_LOCK:
        TARGET_PATH.parent.mkdir(parents=True, exist_ok=True)
        lock_path = TARGET_PATH.with_name(TARGET_PATH.name + ".lock")
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


def _write_target_data_unlocked(data: Dict[str, Any]) -> None:
    TARGET_PATH.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{TARGET_PATH.name}.", dir=TARGET_PATH.parent
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.write("\n")
        descriptor = -1
        os.replace(temporary_path, TARGET_PATH)
    finally:
        if descriptor != -1:
            os.close(descriptor)
        temporary_path.unlink(missing_ok=True)


def _validate_target_key(kind: str) -> None:
    if not isinstance(kind, str) or not kind.strip():
        raise ValueError("target kind must be a non-empty string")


def save_target(user_id: str, page_id: str, page_url: str, kind: str) -> None:
    _validate_target_key(kind)
    with _target_store_lock():
        data = _read_target_data()
        targets = data.get(user_id)
        if not isinstance(targets, dict):
            targets = {}
            data[user_id] = targets
        targets[kind] = {"page_id": page_id, "page_url": page_url}
        _write_target_data_unlocked(data)


def load_target(user_id: str) -> Dict[str, Dict[str, str]]:
    targets = _target_data().get(user_id, {})
    return targets if isinstance(targets, dict) else {}


def build_notion_mcp_auth(
    path: Path = DEFAULT_TOKEN_PATH,
) -> OAuthClientProvider:
    callback: Dict[str, str] = {}

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            values = parse_qs(urlparse(self.path).query, keep_blank_values=True)
            callback.update({key: values.get(key, [""])[0] for key in ("code", "state", "iss", "error")})
            body = b"Authorization received. You can close this window."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    callback_server = HTTPServer(("127.0.0.1", 0), CallbackHandler)
    redirect_uri = f"http://127.0.0.1:{callback_server.server_port}/callback"

    async def redirect_handler(url: str) -> None:
        print(url, flush=True)

    async def callback_handler() -> AuthorizationCodeResult:
        try:
            await asyncio.to_thread(callback_server.handle_request)
        finally:
            callback_server.server_close()
        if callback.get("error"):
            raise RuntimeError(f"Notion MCP authorization failed: {callback['error']}")
        return AuthorizationCodeResult(
            code=callback.get("code", ""),
            state=callback.get("state"),
            iss=callback.get("iss") or None,
        )

    return OAuthClientProvider(
        MCP_OAUTH_SERVER_URL,
        OAuthClientMetadata(
            redirect_uris=[redirect_uri],
            client_name="Local Notion MCP client",
            token_endpoint_auth_method="none",
        ),
        FileTokenStorage(path),
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )


def _schema_text(schema: Any) -> str:
    try:
        return json.dumps(schema, sort_keys=True)
    except TypeError:
        return repr(schema)


def _create_page_arguments(schema: Dict[str, Any], title: str, markdown: str) -> Dict[str, Any]:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        raise ValueError("root properties are missing")

    pages_schema = properties.get("pages")
    if isinstance(pages_schema, dict) and pages_schema.get("type") == "array":
        page_schema = pages_schema.get("items")
        page_properties = page_schema.get("properties") if isinstance(page_schema, dict) else None
        if not isinstance(page_properties, dict):
            raise ValueError("pages.items.properties are missing")

        page: Dict[str, Any] = {}
        page_property_schema = page_properties.get("properties")
        if isinstance(page_property_schema, dict):
            title_properties = page_property_schema.get("properties")
            if page_property_schema.get("additionalProperties") is False and (
                not isinstance(title_properties, dict) or "title" not in title_properties
            ):
                raise ValueError("pages.items.properties.title is missing")
            page["properties"] = {"title": title}

        content_key = next(
            (key for key in ("content", "markdown", "body_markdown") if key in page_properties),
            None,
        )
        if content_key is None:
            raise ValueError("pages.items content field is missing")
        page[content_key] = markdown
        page_required = set(page_schema.get("required", [])) if isinstance(page_schema, dict) else set()
        if "properties" in page_required and "properties" not in page:
            raise ValueError("pages.items.properties cannot be supplied")

        required = set(schema.get("required", []))
        missing = required - {"pages"}
        if "parent" in missing:
            parent_schema = properties.get("parent")
            parent_properties = parent_schema.get("properties") if isinstance(parent_schema, dict) else None
            if isinstance(parent_properties, dict) and "workspace" in parent_properties:
                arguments = {"parent": {"workspace": True}, "pages": [page]}
                missing.remove("parent")
            else:
                arguments = {"pages": [page]}
        else:
            arguments = {"pages": [page]}
        if missing:
            raise ValueError(f"required root fields cannot be supplied: {sorted(missing)}")
        return arguments

    title_key = "title" if "title" in properties else None
    content_key = next(
        (key for key in ("content", "markdown", "body_markdown") if key in properties),
        None,
    )
    if title_key and content_key:
        return {title_key: title, content_key: markdown}
    raise ValueError("no supported title and content fields found")


def _result_values(result: Any) -> list[Any]:
    values: list[Any] = []
    dumped = None
    if isinstance(result, dict):
        values.append(result)
        for key in ("structuredContent", "structured_content"):
            if key in result:
                values.insert(0, result[key])
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        values.append(structured)
    if hasattr(result, "model_dump"):
        dumped = result.model_dump(mode="json")
        values.append(dumped)
        for key in ("structuredContent", "structured_content"):
            if key in dumped:
                values.insert(0, dumped[key])
    content = dumped.get("content", []) if isinstance(dumped, dict) else []
    if not content:
        content = getattr(result, "content", [])
    for block in content:
        text = block.get("text") if isinstance(block, dict) else getattr(block, "text", None)
        if text:
            try:
                values.append(json.loads(text))
            except json.JSONDecodeError:
                pass
    return values


def _page_result(result: Any) -> Dict[str, str] | None:
    def visit(value: Any) -> Dict[str, str] | None:
        if isinstance(value, dict):
            page_id = value.get("page_id") or value.get("created_page_id") or value.get("id")
            page_url = value.get("page_url") or value.get("created_page_url") or value.get("url")
            if page_id and page_url:
                return {"page_id": str(page_id), "page_url": str(page_url)}
            for child in value.values():
                found = visit(child)
                if found:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = visit(child)
                if found:
                    return found
        return None

    for value in _result_values(result):
        found = visit(value)
        if found:
            return found
    return None


class NotionMcpClient:
    def __init__(self, auth_for_user: AuthForUser) -> None:
        self.auth_for_user = auth_for_user

    @asynccontextmanager
    async def _session(self, user_id: str):
        auth = self.auth_for_user(user_id)
        if hasattr(auth, "__await__"):
            auth = await auth
        async with httpx2.AsyncClient(auth=auth, follow_redirects=True) as http:
            async with streamable_http_client(
                MCP_SERVER_URL, http_client=http
            ) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session

    async def fetch_page(self, user_id: str, page_url: str) -> Dict[str, Any]:
        async with self._session(user_id) as session:
            names = {tool.name for tool in (await session.list_tools()).tools}
            tool = "notion-fetch" if "notion-fetch" in names else "fetch"
            if tool not in names:
                raise RuntimeError("Notion MCP fetch tool is unavailable")
            result = await session.call_tool(tool, {"id": page_url})
            if getattr(result, "isError", getattr(result, "is_error", False)):
                raise RuntimeError("Notion MCP fetch failed")
            return result.model_dump(mode="json")

    async def create_page(
        self,
        user_id: str,
        title: str,
        markdown: str,
        remember_as: str | None = None,
    ) -> Dict[str, str]:
        if remember_as is not None:
            _validate_target_key(remember_as)
        tool_name = "notion-create-pages"
        async with self._session(user_id) as session:
            tool = next(
                (tool for tool in (await session.list_tools()).tools if tool.name == tool_name),
                None,
            )
            if tool is None:
                raise RuntimeError(f"Notion MCP tool {tool_name!r} is unavailable; schema: none")
            try:
                arguments = _create_page_arguments(tool.input_schema, title, markdown)
            except (AttributeError, TypeError, ValueError) as error:
                raise RuntimeError(
                    f"Notion MCP tool {tool_name!r} schema cannot be satisfied: {error}; "
                    f"schema: {_schema_text(getattr(tool, 'input_schema', None))}"
                ) from error
            result = await session.call_tool(tool_name, arguments)
            if getattr(result, "isError", getattr(result, "is_error", False)):
                raise RuntimeError(f"Notion MCP tool {tool_name!r} failed")
            page = _page_result(result)
            if page is None:
                raise RuntimeError(
                    f"Notion MCP tool {tool_name!r} returned no page_id/page_url; "
                    f"schema: {_schema_text(tool.input_schema)}"
                )
            if remember_as is not None:
                save_target(user_id, page["page_id"], page["page_url"], remember_as)
            return page

    def target_page(self, user_id: str, kind: str) -> Dict[str, str] | None:
        return load_target(user_id).get(kind)
