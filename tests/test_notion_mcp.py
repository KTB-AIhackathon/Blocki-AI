import asyncio
import io
import json
import stat
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from app.collect.notion import FileTokenStorage, NotionMcpClient, build_notion_mcp_auth


class FakeSession:
    def __init__(self):
        self.calls = []

    async def list_tools(self):
        return SimpleNamespace(
            tools=[
                SimpleNamespace(
                    name="notion-create-pages",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "pages": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "properties": {
                                            "type": "object",
                                            "properties": {"title": {"type": "string"}},
                                        },
                                        "content": {"type": "string"},
                                    },
                                    "required": ["properties", "content"],
                                },
                            }
                        },
                        "required": ["pages"],
                    },
                )
            ]
        )

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return SimpleNamespace(
            isError=False,
            structured_content={
                "pages": [{"id": "page-1", "url": "https://www.notion.so/page-1"}]
            },
        )


class StubNotionMcpClient(NotionMcpClient):
    def __init__(self, session):
        super().__init__(lambda user_id: None)
        self.session = session

    @asynccontextmanager
    async def _session(self, user_id):
        yield self.session


class NotionMcpTests(unittest.TestCase):
    def test_callback_omitted_issuer_is_none(self):
        class FakeServer:
            server_port = 12345

            def __init__(self, _address, handler):
                self.handler = handler

            def handle_request(self):
                request = object.__new__(self.handler)
                request.path = "/callback?code=code&state=state"
                request.send_response = lambda *_args: None
                request.send_header = lambda *_args: None
                request.end_headers = lambda: None
                request.wfile = io.BytesIO()
                request.do_GET()

            def server_close(self):
                pass

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("app.collect.notion.HTTPServer", FakeServer):
                provider = build_notion_mcp_auth(Path(temp_dir) / "tokens.json")
                result = asyncio.run(provider.context.callback_handler())

        self.assertIsNone(result.iss)

    def test_token_storage_round_trips_tokens_and_client_info(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "nested" / "tokens.json"
            storage = FileTokenStorage(path)
            tokens = OAuthToken(access_token="access", refresh_token="refresh", expires_in=3600)
            client_info = OAuthClientInformationFull(
                client_id="client",
                redirect_uris=["http://127.0.0.1:1234/callback"],
                token_endpoint_auth_method="none",
            )

            asyncio.run(storage.set_tokens(tokens))
            asyncio.run(storage.set_client_info(client_info))

            loaded = FileTokenStorage(path)
            self.assertEqual(asyncio.run(loaded.get_tokens()), tokens)
            self.assertEqual(asyncio.run(loaded.get_client_info()), client_info)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(json.loads(path.read_text())["tokens"]["access_token"], "access")

    def test_create_page_calls_runtime_discovered_tool(self):
        session = FakeSession()
        client = StubNotionMcpClient(session)

        result = asyncio.run(client.create_page("user-1", "A title", "# Body"))

        self.assertEqual(session.calls[0][0], "notion-create-pages")
        self.assertEqual(session.calls[0][1]["pages"][0]["properties"]["title"], "A title")
        self.assertEqual(session.calls[0][1]["pages"][0]["content"], "# Body")
        self.assertEqual(
            result,
            {"page_id": "page-1", "page_url": "https://www.notion.so/page-1"},
        )


if __name__ == "__main__":
    unittest.main()
