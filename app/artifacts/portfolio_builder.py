"""Build an evidence-backed Markdown portfolio from normalized TIL data."""

from collections import Counter
import re
import sys
from pathlib import Path
from typing import Any


_TODO_PREFIX = "[TODO: 사용자 입력 필요 — "
_NON_FILLABLE = {
    "🚀 Projects": "프로젝트명, 기간, 팀 구성, 역할, 기술 스택, 링크, 주요 기능, 아키텍처",
    "⚡ Performance Optimization": "최적화 문제, before/after 구조, 지표와 검증 결과",
    "🐛 Troubleshooting": "문제 현상, 원인, 해결 과정, 검증 결과",
    "💬 Real-time Communication": "WebSocket/STOMP 연결 구조와 구현 내용",
    "🧠 Redis": "Redis 사용 이유, 목적, 키 구조, trade-off",
    "🔐 Authentication & Security": "인증 방식, 흐름, 보안 처리",
    "🗄 Database Design": "ERD, 주요 엔티티, 설계 결정과 trade-off",
    "📡 API": "API 엔드포인트, 메서드, 설명",
    "🧪 Load Test": "부하 테스트 목적, 환경, before/after 지표와 분석",
    "🧩 Technical Decisions": "기술·구조 선택, 대안, 이유와 trade-off",
    "🏆 Awards & Activities": "수상 및 활동 이력",
    "📜 Certifications": "자격증 취득 날짜와 이름",
}


def build_portfolio(corpus: dict, template_path: Path) -> dict:
    """Render only claims backed by verifiable local TIL line evidence."""
    corpus_root = corpus.get("_corpus_root")
    template_headings = re.findall(
        r"^#\s+(.+?)\s*$", Path(template_path).read_text(), re.MULTILINE
    )
    headings = []
    for heading in template_headings:
        if heading == "개발자 포트폴리오":
            headings.append(heading)
            headings.append("👋 About Me")
        else:
            headings.append(heading)
    if "👋 About Me" not in headings:
        headings.insert(1, "👋 About Me")

    records = _records(corpus)
    output_evidence = []
    gaps = []

    def todo(section: str, missing: str) -> str:
        gaps.append({"section": section, "missing": missing})
        return f"{_TODO_PREFIX}{missing}]"

    def claim(evidence: dict[str, Any], text: str, section: str) -> str | None:
        if not _valid_evidence(evidence):
            return None
        output_evidence.append({
            "claim": text,
            "source_path": _relative_source_path(evidence["source_path"], corpus_root),
            "source_url": evidence.get("source_url") or evidence.get("_source_url"),
            "line_start": evidence["line_start"],
            "line_end": evidence["line_end"],
            "exact_quote": evidence["exact_quote"],
            "section": section,
        })
        return text

    def render_challenges() -> list[str]:
        lines = []
        for _entry, detail in records:
            detail_evidence = detail.get("_evidence", {})
            for index, challenge in enumerate(detail.get("challenges", [])):
                if not isinstance(challenge, dict):
                    continue
                evidence_item = _at(detail_evidence.get("challenges", []), index)
                challenge_evidence = (
                    evidence_item.get("challenge")
                    if isinstance(evidence_item, dict)
                    else None
                )
                title = str(challenge.get("title", "")).strip()
                if not title or claim(challenge_evidence, title, "🔥 Technical Challenges") is None:
                    lines.append(f"- {todo('🔥 Technical Challenges', '도전 과제 원문과 파일 근거')}")
                    continue
                lines.append(f"- 도전 과제: {title}")
                solution = str(challenge.get("solution") or "").strip()
                solution_evidence = (
                    evidence_item.get("solution")
                    if isinstance(evidence_item, dict)
                    else None
                )
                if solution and claim(
                    solution_evidence, solution, "🔥 Technical Challenges"
                ) is not None:
                    lines.append(f"  - 해결 방법: {solution}")
                lines.append(
                    "  - 근거: "
                    f"{_evidence_reference(challenge_evidence, corpus_root)}"
                )
                lines.append(
                    f"  - {todo('🔥 Technical Challenges', 'before → after 수치, 단위, 검증 방법')}"
                )
        if not lines:
            lines.append(f"- {todo('🔥 Technical Challenges', '도전 과제 원문과 파일 근거')}")
        return lines

    def render_learned() -> list[str]:
        grouped: dict[str, list[tuple[str, dict]]] = {}
        seen = set()
        for entry, detail in records:
            month = _month(entry.get("date") or detail.get("date"))
            for index, topic in enumerate(detail.get("learned", [])):
                if not isinstance(topic, dict):
                    continue
                text = str(topic.get("description") or topic.get("topic") or "").strip()
                if not text or text in seen:
                    continue
                evidence_item = _at(detail.get("_evidence", {}).get("learned", []), index)
                topic_evidence = (
                    evidence_item.get("topic")
                    if isinstance(evidence_item, dict)
                    else None
                )
                if not _valid_evidence(topic_evidence):
                    continue
                seen.add(text)
                grouped.setdefault(month, []).append((text, topic_evidence))
        lines = []
        for month in sorted(grouped, reverse=True):
            lines.append(f"## {month}")
            for text, evidence in grouped[month]:
                claim(evidence, text, "📚 What I Learned")
                lines.append(f"- {text}")
                lines.append(
                    "  - 근거: "
                    f"{_evidence_reference(evidence, corpus_root)}"
                )
        if not lines:
            lines.append(todo("📚 What I Learned", "학습 주제와 파일 근거"))
        return lines

    def render_retrospective() -> list[str]:
        items = []
        for entry, detail in records:
            for index, text in enumerate(detail.get("retrospective", [])):
                evidence_item = _at(detail.get("_evidence", {}).get("retrospective", []), index)
                if isinstance(evidence_item, dict) and _valid_evidence(evidence_item):
                    items.append((
                        _date_key(entry.get("date") or detail.get("date")),
                        str(text).strip(),
                        evidence_item,
                    ))
        lines = []
        for _date, text, evidence in sorted(items, key=lambda item: item[0], reverse=True)[:10]:
            claim(evidence, text, "🎯 Retrospective")
            lines.append(f"- {text}")
            lines.append(
                "  - 근거: "
                f"{_evidence_reference(evidence, corpus_root)}"
            )
        if not lines:
            lines.append(todo("🎯 Retrospective", "회고 내용과 파일 근거"))
        return lines

    def render_growth() -> list[str]:
        seen = set()
        lines = []
        for _entry, detail in records:
            for index, text in enumerate(detail.get("scrum", [])):
                text = str(text).strip()
                if not text or text in seen:
                    continue
                evidence_item = _at(detail.get("_evidence", {}).get("scrum", []), index)
                if claim(evidence_item, text, "📈 Growth") is None:
                    continue
                seen.add(text)
                lines.append(f"- {text}")
                lines.append(
                    "  - 근거: "
                    f"{_evidence_reference(evidence_item, corpus_root)}"
                )
        if not lines:
            lines.append(todo("📈 Growth", "스크럼 목표와 파일 근거"))
        return lines

    def render_stack() -> list[str]:
        terms = Counter()
        first_evidence = {}
        for _entry, detail in records:
            for index, topic in enumerate(detail.get("learned", [])):
                if not isinstance(topic, dict):
                    continue
                value = str(topic.get("description") or topic.get("topic") or "")
                evidence_item = _at(detail.get("_evidence", {}).get("learned", []), index)
                evidence_item = evidence_item.get("topic") if isinstance(evidence_item, dict) else None
                _count_terms(value, evidence_item, terms, first_evidence)
        for entry, _detail in records:
            for evidence_item in entry.get("_week_topic_evidence", []):
                _count_terms(
                    _strip_index_bullet(evidence_item.get("exact_quote", "")),
                    evidence_item,
                    terms,
                    first_evidence,
                )
        lines = ["## 관찰된 기술 (등장 횟수)"]
        for term, count in terms.most_common():
            evidence_item = first_evidence[term]
            claim(evidence_item, f"{term} ({count})", "🛠 Tech Stack")
            lines.append(f"- {term} ({count})")
        lines.append(
            f"- {todo('🛠 Tech Stack', '관찰된 기술을 Backend/Frontend/Database/Infrastructure 등으로 분류')}"
        )
        return lines

    def render_section(heading: str) -> list[str]:
        if heading == "👋 About Me":
            return [todo(heading, "이름, 직무, 관심 분야, 개발 철학")]
        if heading == "🔥 Technical Challenges":
            return render_challenges()
        if heading == "📚 What I Learned":
            return render_learned()
        if heading == "🎯 Retrospective":
            return render_retrospective()
        if heading == "🛠 Tech Stack":
            return render_stack()
        if heading == "📈 Growth":
            return render_growth()
        if heading == "📞 Contact":
            return [todo(heading, "이메일, GitHub, Blog, LinkedIn 연락처")]
        if heading in _NON_FILLABLE:
            return [todo(heading, _NON_FILLABLE[heading])]
        return [todo(heading, "이 섹션의 사용자 입력")]

    blocks = []
    for heading in headings:
        blocks.append(f"# {heading}")
        if heading != "개발자 포트폴리오":
            blocks.extend(render_section(heading))
    return {
        "markdown": "\n\n".join(blocks) + "\n",
        "evidence": output_evidence,
        "gaps": gaps,
    }


def _records(corpus: dict) -> list[tuple[dict, dict]]:
    records = []
    for entry in corpus.get("entries", []):
        if not isinstance(entry, dict) or not isinstance(entry.get("detail"), dict):
            continue
        detail = entry["detail"]
        source_url = entry.get("source_url") or entry.get("detail_url")
        _attach_source_url(detail.get("_evidence"), source_url)
        _attach_source_url(entry.get("_week_topic_evidence"), source_url)
        records.append((entry, detail))
    return sorted(
        records,
        key=lambda record: _date_key(record[0].get("date") or record[1].get("date")),
        reverse=True,
    )


def _at(values: Any, index: int) -> Any:
    return values[index] if isinstance(values, list) and index < len(values) else None


def _attach_source_url(value: Any, source_url: Any) -> None:
    if isinstance(value, dict):
        if "source_path" in value:
            value["_source_url"] = source_url
            return
        for child in value.values():
            _attach_source_url(child, source_url)
    elif isinstance(value, list):
        for child in value:
            _attach_source_url(child, source_url)


def _relative_source_path(source_path: str, corpus_root: Any) -> str:
    path = Path(source_path)
    if not path.is_absolute() or not corpus_root:
        return path.as_posix()
    try:
        return path.relative_to(Path(corpus_root)).as_posix()
    except ValueError:
        return path.as_posix()


def _evidence_reference(evidence: dict, corpus_root: Any) -> str:
    location = f"{_relative_source_path(evidence['source_path'], corpus_root)}:{evidence['line_start']}"
    if evidence["line_end"] != evidence["line_start"]:
        location += f"-{evidence['line_end']}"
    source_url = evidence.get("source_url") or evidence.get("_source_url")
    return f"[{location}](<{source_url}>)" if source_url else location


def _valid_evidence(evidence: Any) -> bool:
    if not isinstance(evidence, dict):
        return False
    required = ("source_path", "line_start", "line_end", "exact_quote")
    if any(not evidence.get(key) for key in required):
        return False
    if not isinstance(evidence["line_start"], int) or not isinstance(evidence["line_end"], int):
        return False
    if evidence["line_start"] < 1 or evidence["line_end"] < evidence["line_start"]:
        return False
    path = Path(evidence["source_path"])
    if not path.is_file():
        return False
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return False
    if evidence["line_end"] > len(lines):
        return False
    return evidence["exact_quote"] in lines[evidence["line_start"] - 1]


def _date_key(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    match = re.search(r"(\d{4})[-.](\d{2})[-.](\d{2})", value)
    if match:
        return "-".join(match.groups())
    match = re.search(r"(\d{2})[-.](\d{2})[-.](\d{2})", value)
    return f"20{match.group(1)}-{match.group(2)}-{match.group(3)}" if match else value


def _month(value: Any) -> str:
    key = _date_key(value)
    return key[:7] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", key) else "날짜 미상"


def _strip_index_bullet(value: str) -> str:
    return re.sub(r"^\s*[-*]\s+", "", value).strip()


def _count_terms(value: str, evidence: dict | None, counts: Counter, first_evidence: dict):
    if not _valid_evidence(evidence):
        return
    cleaned = re.sub(r"^\s*#+\s*", "", value)
    cleaned = re.sub(r"^주제\s*\d+\s*[:：]\s*", "", cleaned).strip()
    terms = re.findall(r"[A-Za-z][A-Za-z0-9+#./-]*|[가-힣]{2,}", cleaned)
    for term in terms:
        if term in {"주제", "오늘의", "새로", "배운", "내용"} or term.isdigit():
            continue
        counts[term] += 1
        first_evidence.setdefault(term, evidence)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 3:
        raise SystemExit("usage: python portfolio_builder.py <til_root> <template> <out.md>")
    from notion_agent import load_til_corpus

    result = build_portfolio(load_til_corpus(Path(argv[0])), Path(argv[1]))
    Path(argv[2]).write_text(result["markdown"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
