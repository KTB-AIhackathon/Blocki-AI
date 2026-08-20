import re
import tempfile
import unittest
from pathlib import Path

from app.artifacts.notion_til import _parse_til_index, load_til_corpus, parse_til_detail
from app.artifacts.portfolio_builder import build_portfolio


REAL_ROOT = Path("/Users/hwangsubin/Desktop/sky.kim-til")
TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "developer-portfolio.md"


class ParserChangeTests(unittest.TestCase):
    def test_detail_accepts_all_supported_date_heading_levels(self):
        for marker in ("#", "##", "###"):
            with self.subTest(marker=marker):
                self.assertEqual(
                    parse_til_detail(f"{marker} 날짜: 2024-04-05\n")["date"],
                    "2024-04-05",
                )

    def test_detail_accepts_date_heading_levels_and_attaches_sibling_solution(self):
        result = parse_til_detail(
            "# 날짜: 2024-04-05\n"
            "### 새로 배운 내용\n"
            "### 주제 1: 하위 호환 주제\n"
            "- 상세\n"
            "### 보충 설명\n"
            "- 보충은 미분류\n"
            "### 주제 2: 이어지는 주제\n"
            "### 오늘의 도전 과제와 해결 방법\n"
            "- 도전 과제 1: 원인 찾기\n"
            "- 해결 방법: 로그를 확인함\n"
            "- 도전 과제 2: 재현하기\n"
            "### 오늘의 회고\n"
            "- 회고\n"
        )

        self.assertEqual(result["date"], "2024-04-05")
        self.assertEqual(result["learned"][0]["topic"], "주제 1")
        self.assertEqual(result["learned"][0]["description"], "하위 호환 주제")
        self.assertEqual(result["learned"][1]["description"], "이어지는 주제")
        self.assertEqual(result["unparsed_sections"], ["보충 설명"])
        self.assertEqual(result["challenges"], [
            {
                "title": "원인 찾기",
                "project": None,
                "result": None,
                "solution": "로그를 확인함",
            },
            {
                "title": "재현하기",
                "project": None,
                "result": None,
                "solution": None,
            },
        ])

    def test_index_accepts_plain_entries_week_topics_and_empty_week_topic(self):
        result = _parse_til_index(
            "### [12주차]\n"
            "- Kubernetes, Helm\n"
            "26.07.31 [제목](https://example.com/2026-07-31.md)\n"
            "### [11주차]: 네트워크\n"
            "- 26.07.30 기존 형식 ([상세](detail))\n"
        )

        self.assertEqual(result[0]["week_label"], "12주차")
        self.assertIsNone(result[0]["week_topic"])
        self.assertEqual(result[0]["week_topics"], ["Kubernetes, Helm"])
        self.assertEqual(result[0]["title"], "제목")
        self.assertEqual(result[0]["detail_url"], "https://example.com/2026-07-31.md")
        self.assertEqual(result[1]["week_topic"], "네트워크")
        self.assertEqual(result[1]["week_topics"], [])

    def test_load_til_corpus_reads_local_files_and_attaches_line_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            daily = root / "Jul" / "1week" / "2024-07-01.md"
            daily.parent.mkdir(parents=True)
            daily.write_text(
                "# 날짜: 2024-07-01\n"
                "### 스크럼\n"
                "- 목표\n"
                "### 새로 배운 내용\n"
                "#### 주제 1: Python\n"
                "### 오늘의 도전 과제와 해결 방법\n"
                "- 도전 과제 1: 테스트 작성\n"
                "- 해결 방법: unittest 사용\n"
                "### 오늘의 회고\n"
                "- 회고\n"
            )
            (root / "README.md").write_text(
                "### [1주차]\n"
                "- Python\n"
                "24.07.01 [테스트](https://example.com/2024-07-01.md)\n"
            )

            corpus = load_til_corpus(root)

        self.assertEqual(corpus["errors"], [])
        detail = corpus["entries"][0]["detail"]
        self.assertEqual(detail["date"], "2024-07-01")
        evidence = detail["_evidence"]["challenges"][0]["challenge"]
        self.assertEqual(evidence["line_start"], 7)
        self.assertEqual(evidence["line_end"], 7)
        self.assertEqual(evidence["exact_quote"], "- 도전 과제 1: 테스트 작성")
        self.assertIn(str(daily), evidence["source_path"])
        self.assertEqual(
            corpus["entries"][0]["source_url"],
            "https://example.com/2024-07-01.md",
        )

    def test_evidence_uses_relative_path_and_linked_single_line_reference(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            daily = root / "Jul" / "1week" / "2024-07-01.md"
            daily.parent.mkdir(parents=True)
            daily.write_text("#### 주제 1: Python\n")
            output = build_portfolio(
                {
                    "_corpus_root": str(root),
                    "entries": [{
                        "source_url": "https://example.com/2024-07-01.md",
                        "detail": {
                            "date": "2024-07-01",
                            "learned": [{"topic": "주제 1", "description": "Python"}],
                            "_evidence": {
                                "learned": [{"topic": {
                                    "source_path": str(daily),
                                    "line_start": 1,
                                    "line_end": 1,
                                    "exact_quote": "#### 주제 1: Python",
                                }}],
                            },
                        },
                    }],
                    "errors": [],
                },
                TEMPLATE,
            )

        self.assertEqual(output["evidence"][0]["source_path"], "Jul/1week/2024-07-01.md")
        self.assertEqual(output["evidence"][0]["source_url"], "https://example.com/2024-07-01.md")
        self.assertIn(
            "근거: [Jul/1week/2024-07-01.md:1](<https://example.com/2024-07-01.md>)",
            output["markdown"],
        )


class PortfolioBuilderTests(unittest.TestCase):
    def test_fail_closed_drops_challenge_without_evidence(self):
        output = build_portfolio(
            {
                "entries": [{
                    "date": "2024-01-01",
                    "detail": {
                        "challenges": [{
                            "title": "비공개 도전",
                            "project": None,
                            "result": None,
                            "solution": None,
                        }],
                    },
                }],
                "errors": [],
            },
            TEMPLATE,
        )

        self.assertNotIn("비공개 도전", output["markdown"])
        self.assertIn("[TODO: 사용자 입력 필요", output["markdown"])

    @unittest.skipUnless(REAL_ROOT.exists(), "real TIL corpus is not available")
    def test_real_corpus_is_evidence_backed_and_shows_all_gaps(self):
        corpus = load_til_corpus(REAL_ROOT)
        output = build_portfolio(corpus, TEMPLATE)
        markdown = output["markdown"]

        self.assertGreaterEqual(len(output["evidence"]), 40)
        self.assertGreaterEqual(
            sum(item["source_url"].startswith("https://github.com/") for item in output["evidence"]),
            40,
        )
        for item in output["evidence"]:
            self.assertFalse(Path(item["source_path"]).is_absolute())
            lines = (REAL_ROOT / item["source_path"]).read_text().splitlines()
            self.assertIn(item["exact_quote"], lines[item["line_start"] - 1])
        challenge_section = markdown.split("# 🔥 Technical Challenges", 1)[1].split("# ", 1)[0]
        challenges = re.findall(r"^- 도전 과제:", challenge_section, re.MULTILINE)
        metric_todos = challenge_section.count(
            "[TODO: 사용자 입력 필요 — before → after 수치, 단위, 검증 방법]"
        )
        self.assertTrue(challenges)
        self.assertEqual(len(challenges), metric_todos)
        for heading in (
            "🚀 Projects",
            "⚡ Performance Optimization",
            "🐛 Troubleshooting",
            "💬 Real-time Communication",
            "🧠 Redis",
            "🔐 Authentication & Security",
            "🗄 Database Design",
            "📡 API",
            "🧪 Load Test",
            "🧩 Technical Decisions",
            "🏆 Awards & Activities",
            "📜 Certifications",
        ):
            section = markdown.split(f"# {heading}", 1)[1].split("# ", 1)[0]
            self.assertIn("[TODO: 사용자 입력 필요", section)


if __name__ == "__main__":
    unittest.main()
