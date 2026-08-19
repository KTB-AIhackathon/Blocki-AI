import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from app.artifacts.notion_til import build_notion_agent


PAGE_URL = "https://app.notion.com/p/ebe0ffe9306d82329a928189e78f66d2"


class NotionAgentTests(unittest.TestCase):
    def test_user_scoped_mcp_fetch_writes_json_and_markdown(self):
        calls = []

        async def fetch_page(user_id, page_url):
            calls.append((user_id, page_url))
            return {
                "title": "주간 일정",
                "text": (
                    "<column>\n## 월요일\n- [x] 포트폴리오 정리\n"
                    "\t- [ ] 결과 링크 추가\n</column>"
                ),
            }

        with tempfile.TemporaryDirectory() as temp_dir:
            graph = build_notion_agent(fetch_page, Path(temp_dir))
            result = asyncio.run(
                graph.ainvoke({"user_id": "user-1", "page_url": PAGE_URL})
            )

            payload = json.loads(Path(result["json_path"]).read_text())
            markdown = Path(result["markdown_path"]).read_text()

        self.assertEqual(calls, [("user-1", PAGE_URL)])
        self.assertEqual(payload["source"], "notion_mcp")
        self.assertEqual(payload["user_id"], "user-1")
        self.assertEqual(payload["page_url"], PAGE_URL)
        self.assertEqual(payload["content"]["title"], "주간 일정")
        self.assertEqual(
            payload["tasks"],
            [
                {
                    "day": "월요일",
                    "title": "포트폴리오 정리",
                    "completed": True,
                    "parent": None,
                },
                {
                    "day": "월요일",
                    "title": "결과 링크 추가",
                    "completed": False,
                    "parent": "포트폴리오 정리",
                },
            ],
        )
        self.assertIn("주간 일정", markdown)
        self.assertIn("포트폴리오 정리", markdown)
        self.assertNotIn("<column>", markdown)

    def test_missing_user_id_is_rejected_before_mcp_call(self):
        called = False

        async def fetch_page(user_id, page_url):
            nonlocal called
            called = True
            return {}

        with tempfile.TemporaryDirectory() as temp_dir:
            graph = build_notion_agent(fetch_page, Path(temp_dir))
            with self.assertRaises(ValueError):
                asyncio.run(graph.ainvoke({"user_id": "", "page_url": PAGE_URL}))

        self.assertFalse(called)


if __name__ == "__main__":
    unittest.main()
