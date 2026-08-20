import asyncio
from concurrent.futures import ThreadPoolExecutor
import io
import json
import multiprocessing
import stat
import tempfile
import time
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from app.collect import notion as notion_mcp
from app.collect.notion import (
    FileTokenStorage,
    NotionMcpClient,
    build_notion_mcp_auth,
    load_target,
    save_target,
)


_TARGET_PROCESS_BARRIER = None
_ORIGINAL_TARGET_WRITE = None


def _save_target_from_process(index):
    _TARGET_PROCESS_BARRIER.wait(timeout=5)
    notion_mcp.save_target("user-1", f"page-{index}", f"url-{index}", f"kind-{index}")


def _slow_target_write(data):
    time.sleep(0.02)
    _ORIGINAL_TARGET_WRITE(data)


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

    def test_target_store_round_trips_by_user_and_kind(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("app.collect.notion.TARGET_PATH", Path(temp_dir) / "targets.json"):
                save_target("user-1", "page-1", "https://notion/page-1", "til_index")

                self.assertEqual(
                    load_target("user-1"),
                    {"til_index": {"page_id": "page-1", "page_url": "https://notion/page-1"}},
                )
                self.assertEqual(stat.S_IMODE((Path(temp_dir) / "targets.json").stat().st_mode), 0o600)

    def test_create_page_remember_as_persists_target(self):
        session = FakeSession()
        client = StubNotionMcpClient(session)

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("app.collect.notion.TARGET_PATH", Path(temp_dir) / "targets.json"):
                client_result = asyncio.run(client.create_page("user-1", "A title", "# Body", remember_as="til_index"))

                self.assertEqual(client.target_page("user-1", "til_index"), client_result)

    def test_target_store_serializes_concurrent_saves_without_losing_entries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target_path = Path(temp_dir) / "targets.json"
            original_write = notion_mcp._write_target_data_unlocked

            def slow_write(data):
                time.sleep(0.02)
                original_write(data)

            with patch("app.collect.notion.TARGET_PATH", target_path), patch(
                "app.collect.notion._write_target_data_unlocked", side_effect=slow_write
            ), ThreadPoolExecutor(max_workers=8) as executor:
                list(executor.map(
                    lambda index: save_target("user-1", f"page-{index}", f"url-{index}", f"kind-{index}"),
                    range(8),
                ))
                self.assertEqual(
                    set(load_target("user-1")),
                    {f"kind-{index}" for index in range(8)},
                )

    def test_target_store_serializes_concurrent_process_saves_without_losing_entries(self):
        if "fork" not in multiprocessing.get_all_start_methods():
            self.skipTest("requires fork")

        with tempfile.TemporaryDirectory() as temp_dir:
            target_path = Path(temp_dir) / "targets.json"
            context = multiprocessing.get_context("fork")
            barrier = context.Barrier(8)
            global _TARGET_PROCESS_BARRIER, _ORIGINAL_TARGET_WRITE
            _TARGET_PROCESS_BARRIER = barrier
            _ORIGINAL_TARGET_WRITE = notion_mcp._write_target_data_unlocked
            processes = []
            try:
                with patch("app.collect.notion.TARGET_PATH", target_path), patch(
                    "app.collect.notion._write_target_data_unlocked", side_effect=_slow_target_write
                ):
                    processes = [
                        context.Process(target=_save_target_from_process, args=(index,))
                        for index in range(8)
                    ]
                    for process in processes:
                        process.start()
                    for process in processes:
                        process.join(5)
                    self.assertTrue(all(process.exitcode == 0 for process in processes))
                    self.assertEqual(
                        set(load_target("user-1")),
                        {f"kind-{index}" for index in range(8)},
                    )
            finally:
                _TARGET_PROCESS_BARRIER = None
                _ORIGINAL_TARGET_WRITE = None

    def test_target_store_recovers_from_invalid_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target_path = Path(temp_dir) / "targets.json"
            target_path.write_text("not json")

            with patch("app.collect.notion.TARGET_PATH", target_path):
                self.assertEqual(load_target("user-1"), {})
                save_target("user-1", "page-1", "url-1", "til_index")
                self.assertEqual(load_target("user-1")["til_index"]["page_id"], "page-1")

    def test_target_store_ignores_invalid_user_value_shape(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target_path = Path(temp_dir) / "targets.json"
            target_path.write_text(json.dumps({"user-1": ["not a target map"]}))

            with patch("app.collect.notion.TARGET_PATH", target_path):
                self.assertEqual(load_target("user-1"), {})

    def test_remember_as_must_be_a_non_empty_string_key(self):
        session = FakeSession()
        client = StubNotionMcpClient(session)

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("app.collect.notion.TARGET_PATH", Path(temp_dir) / "targets.json"):
                with self.assertRaises(ValueError):
                    asyncio.run(client.create_page("user-1", "A title", "# Body", remember_as=""))
                with self.assertRaises(ValueError):
                    save_target("user-1", "page-1", "url-1", "")
                with self.assertRaises(ValueError):
                    save_target("user-1", "page-1", "url-1", [])

        self.assertEqual(session.calls, [])


if __name__ == "__main__":
    unittest.main()
