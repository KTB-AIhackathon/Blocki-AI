import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.artifacts.notion_til import (
    _page_text,
    _parse_til_index,
    build_notion_agent,
    collect_til,
    parse_til_detail,
)


PAGE_URL = "https://app.notion.com/p/ebe0ffe9306d82329a928189e78f66d2"


class NotionAgentTests(unittest.TestCase):
    def test_user_scoped_mcp_fetch_writes_json_and_markdown(self):
        calls = []

        async def fetch_page(user_id, page_url):
            calls.append((user_id, page_url))
            return {
                "title": "TIL",
                "text": (
                    "## 날짜: 2024-04-05\n\n"
                    "### 스크럼\n- 목표\n\n"
                    "### 새로 배운 내용\n#### 주제 1: 설명\n- 상세\n"
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
        self.assertEqual(payload["content"]["title"], "TIL")
        self.assertEqual(payload["til"]["date"], "2024-04-05")
        self.assertEqual(payload["til"]["learned"][0]["topic"], "주제 1")
        self.assertIn("TIL", markdown)
        self.assertIn("주제 1", markdown)

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

    def test_parse_til_detail_template(self):
        markdown = (Path(__file__).resolve().parents[1] / "templates" / "til-daily.md").read_text()

        result = parse_til_detail(markdown)

        self.assertEqual(result["date"], "YYYY-MM-DD")
        self.assertEqual(result["scrum"], ["학습 목표 1 : 아침 스크럼에 작성한 내용 붙여넣기", "학습 목표 2", "학습 목표 3"])
        self.assertEqual(len(result["learned"]), 2)
        self.assertEqual(result["learned"][0]["topic"], "주제 1")
        self.assertEqual(result["learned"][0]["description"], "주제에 대한 설명")
        self.assertEqual(len(result["learned"][0]["details"]), 3)
        self.assertEqual(len(result["challenges"]), 2)
        self.assertEqual(result["challenges"][0]["project"], "어느 프로젝트의 과제인지")
        self.assertEqual(result["challenges"][0]["result"], "무엇이 얼마에서 얼마로 바뀌었는지")
        self.assertIsNone(result["challenges"][1]["project"])
        self.assertIsNone(result["challenges"][1]["result"])
        self.assertEqual(len(result["retrospective"]), 2)
        self.assertEqual(result["references"][0], {"title": "링크 제목", "url": "URL"})
        self.assertEqual(result["unparsed_sections"], [])

    def test_parse_til_detail_real_notion_fetch_envelope(self):
        payload = json.loads(Path("tests/fixtures/notion-fetch-til-detail.json").read_text())

        result = parse_til_detail(_page_text(payload))

        self.assertEqual(result, {
            "date": "YYYY-MM-DD",
            "scrum": [
                "학습 목표 1 : 아침 스크럼에 작성한 내용 붙여넣기",
                "학습 목표 2",
                "학습 목표 3",
            ],
            "learned": [
                {
                    "topic": "주제 1",
                    "description": "주제에 대한 설명",
                    "details": ["상세 내용 1", "상세 내용 2", "상세 내용 3"],
                },
                {
                    "topic": "주제 2",
                    "description": "주제에 대한 설명",
                    "details": ["상세 내용 1", "상세 내용 2"],
                },
            ],
            "challenges": [
                {
                    "title": "도전 과제에 대한 설명 및 해결 방법",
                    "project": "어느 프로젝트의 과제인지",
                    "result": "무엇이 얼마에서 얼마로 바뀌었는지",
                    "solution": None,
                },
                {
                    "title": "도전 과제에 대한 설명 및 해결 방법",
                    "project": None,
                    "result": None,
                    "solution": None,
                },
            ],
            "retrospective": [
                "오늘의 학습 경험에 대한 자유로운 생각이나 느낀 점을 기록합니다.",
                "성공적인 점, 개선해야 할 점, 새롭게 시도하고 싶은 방법 등을 포함할 수 있습니다.",
            ],
            "references": [
                {"title": "링크 제목", "url": "URL"},
                {"title": "링크 제목", "url": "URL"},
            ],
            "unparsed_sections": [],
        })

    def test_parse_til_detail_tolerates_round_trip_markdown(self):
        markdown = r"""## 날짜: 2024-04-05

### 스크럼
* 목표

### 새로 배운 내용
#### 주제 1
* 상세

### 기타
* 보관할 내용

### 참고 자료 및 링크
* \[문서\](https://example.com)
* raw reference
"""

        result = parse_til_detail(markdown)

        self.assertEqual(result["date"], "2024-04-05")
        self.assertEqual(result["scrum"], ["목표"])
        self.assertEqual(result["learned"], [{"topic": "주제 1", "description": None, "details": ["상세"]}])
        self.assertEqual(result["references"], [
            {"title": "문서", "url": "https://example.com"},
            {"title": "raw reference", "url": None},
        ])
        self.assertEqual(result["unparsed_sections"], ["기타"])

    def test_parse_til_detail_ignores_orphan_metadata_and_accepts_fullwidth_colons(self):
        markdown = (
            "## 날짜： 2024-04-05\r\n"
            "### 오늘의 도전 과제와 해결 방법\r\n"
            "- 프로젝트： orphan\u00a0\r\n"
            "- 결과： orphan\r\n"
            "- 콜론 없는 제목\r\n"
            "- 도전 과제: 설명: 내부\r\n"
            "\t- 프로젝트： 앱\r\n"
            "\t- 결과： 통과\r\n"
        )

        result = parse_til_detail(markdown)

        self.assertEqual(result["date"], "2024-04-05")
        self.assertEqual(result["challenges"], [
            {"title": "콜론 없는 제목", "project": None, "result": None, "solution": None},
            {"title": "설명: 내부", "project": "앱", "result": "통과", "solution": None},
        ])
        self.assertFalse(any(key in {"프로젝트", "결과"} for key in result))

    def test_parse_til_detail_keeps_reference_link_with_trailing_text(self):
        result = parse_til_detail(
            "### 참고 자료 및 링크\n- [문서](https://example.com) 읽을거리\n"
        )

        self.assertEqual(result["references"], [{
            "title": "문서 읽을거리",
            "url": "https://example.com",
        }])

    def test_parse_til_detail_ignores_headings_without_required_space(self):
        result = parse_til_detail("###스크럼\n- 잘못된 heading\n")

        self.assertEqual(result["scrum"], [])

    def test_parse_til_detail_does_not_leak_after_malformed_section_heading(self):
        result = parse_til_detail(
            "### 스크럼\n"
            "- 정상 내용\n"
            "###스크럼\n"
            "- 잘못된 heading 뒤 내용\n"
        )

        self.assertEqual(result["scrum"], ["정상 내용"])

    def test_parse_til_detail_keeps_wrong_section_content_in_that_section(self):
        result = parse_til_detail(
            "### 스크럼\n"
            "#### 주제: 잘못된 위치\n"
            "- 프로젝트: 스크럼 내용\n"
            "- 결과: 더 많은 스크럼 내용\n"
            "### 스크럼\n"
            "- 두 번째 스크럼\n"
            "### 오늘의 도전 과제와 해결 방법\n"
            "- 실제 도전\n"
        )

        self.assertEqual(result["learned"], [])
        self.assertEqual(result["scrum"], [
            "프로젝트: 스크럼 내용",
            "결과: 더 많은 스크럼 내용",
            "두 번째 스크럼",
        ])
        self.assertEqual(result["challenges"], [{
            "title": "실제 도전",
            "project": None,
            "result": None,
            "solution": None,
        }])

    def test_page_text_reads_all_content_tags_and_non_dict_json(self):
        tagged = {
            "content": [{
                "type": "text",
                "text": json.dumps({"text": "<content>first</content><content>second</content>"}),
            }],
        }
        non_dict = {
            "content": [{
                "type": "text",
                "text": json.dumps(["raw"]),
            }],
        }

        self.assertEqual(_page_text(tagged), "first\nsecond")
        self.assertEqual(_page_text(non_dict), '["raw"]')
        self.assertEqual(_page_text({
            "content": [
                {"type": "text", "text": json.dumps({"text": "a"})},
                {"type": "text", "text": json.dumps({"text": "b"})},
            ],
        }), "a\nb")

    def test_page_text_preserves_markdown_boundaries_between_text_items(self):
        result = _page_text({
            "content": [
                {"type": "text", "text": json.dumps({"text": "### 스크럼"})},
                {"type": "text", "text": json.dumps({"text": "- 목표"})},
            ],
        })

        self.assertEqual(parse_til_detail(result)["scrum"], ["목표"])

    def test_parse_til_index_accepts_bare_parenthesized_links(self):
        result = _parse_til_index(
            "## [1주차] : html\n- 24.04.01 첫 번째 (https://example.com/detail)\n"
        )

        self.assertEqual(result, [{
            "week_label": "1주차",
            "week_topic": "html",
            "week_topics": [],
            "date": "24.04.01",
            "title": "첫 번째",
            "detail_url": "https://example.com/detail",
        }])

    def test_parse_til_index_rejects_week_heading_without_topic(self):
        result = _parse_til_index(
            "## [1주차] :\n- 24.04.01 should be ignored ([상세](detail))\n"
            "## [2주차] : css\n- 24.04.02 kept ([상세](detail-2))\n"
        )

        self.assertEqual([entry["date"] for entry in result], ["24.04.02"])

    def test_parse_til_index_skips_invalid_dates_and_bullets_before_a_week(self):
        result = _parse_til_index(
            "- 24.04.01 before week ([상세](before))\n"
            "## [1주차] : html\n"
            "- 2024/04/01 invalid date ([상세](invalid))\n"
            "- 2024-04-01 valid date ([상세](valid))\n"
        )

        self.assertEqual([entry["detail_url"] for entry in result], ["valid"])

    def test_collect_til_fetches_all_details_and_records_failures(self):
        calls = []
        index = """## [1주차] : html
- 24.04.01 첫 번째 ([상세](detail-1))
- 24.04.02 두 번째 ([상세](detail-2))

## [2주차] : css
* 24.04.03 세 번째 ([상세](detail-3))
* 24.04.04 네 번째 ([상세](detail-4))
"""

        async def fetch_page(user_id, page_url):
            calls.append((user_id, page_url))
            if page_url == "index":
                return {"text": index}
            if page_url == "detail-2":
                raise RuntimeError("detail unavailable")
            return {"text": f"## 날짜: {page_url}\n### 스크럼\n- 목표"}

        result = asyncio.run(collect_til(fetch_page, "user-1", "index"))

        self.assertEqual(len(result["entries"]), 4)
        self.assertEqual(result["entries"][0]["week_label"], "1주차")
        self.assertEqual(result["entries"][0]["week_topic"], "html")
        self.assertEqual(result["entries"][0]["date"], "24.04.01")
        self.assertEqual(result["entries"][0]["title"], "첫 번째")
        self.assertEqual(result["entries"][0]["detail_url"], "detail-1")
        self.assertEqual(result["entries"][0]["detail"]["date"], "detail-1")
        self.assertEqual(result["errors"], [{"url": "detail-2", "error": "detail unavailable"}])
        self.assertEqual(len([call for call in calls if call[1] != "index"]), 4)

    def test_collect_til_contains_parse_failures_without_killing_gather(self):
        index = (
            "## [1주차] : html\n"
            "- 24.04.01 첫 번째 ([상세](detail-1))\n"
            "- 24.04.02 두 번째 ([상세](detail-2))\n"
        )

        async def fetch_page(_user_id, page_url):
            if page_url == "index":
                return {"text": index}
            return {"text": f"## 날짜: {page_url}"}

        original_parse = parse_til_detail

        def parse(value):
            if "detail-1" in value:
                raise ValueError("malformed detail")
            return original_parse(value)

        with patch("app.artifacts.notion_til.parse_til_detail", side_effect=parse):
            result = asyncio.run(collect_til(fetch_page, "user-1", "index"))

        self.assertEqual([entry["detail"] for entry in result["entries"]], [None, {"date": "detail-2", "scrum": [], "learned": [], "challenges": [], "retrospective": [], "references": [], "unparsed_sections": []}])
        self.assertEqual(result["errors"], [{"url": "detail-1", "error": "malformed detail"}])

    def test_collect_til_deduplicates_detail_fetches_and_preserves_order(self):
        calls = []
        index = (
            "## [1주차] : html\n"
            "- 24.04.01 첫 번째 ([상세](same-detail))\n"
            "- 24.04.02 두 번째 ([상세](same-detail))\n"
        )

        async def fetch_page(_user_id, page_url):
            calls.append(page_url)
            if page_url == "index":
                return {"text": index}
            return {"text": f"## 날짜: {page_url}"}

        result = asyncio.run(collect_til(fetch_page, "user-1", "index", limit=2))

        self.assertEqual([entry["title"] for entry in result["entries"]], ["첫 번째", "두 번째"])
        self.assertEqual([entry["detail"]["date"] for entry in result["entries"]], ["same-detail", "same-detail"])
        self.assertEqual(calls.count("same-detail"), 1)

    def test_collect_til_rejects_negative_limit(self):
        async def fetch_page(_user_id, page_url):
            if page_url == "index":
                return {"text": "## [1주차] : html\n- 24.04.01 첫 번째 (detail)\n"}
            return {"text": "## 날짜: detail"}

        with self.assertRaises(ValueError):
            asyncio.run(collect_til(fetch_page, "user-1", "index", limit=-1))


if __name__ == "__main__":
    unittest.main()
