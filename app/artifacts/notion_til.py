"""LangGraph workflow for user-scoped Notion MCP exports."""

import asyncio
import hashlib
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, TypedDict

from langgraph.graph import END, START, StateGraph


FetchPage = Callable[[str, str], Awaitable[Dict[str, Any]]]
_DETAIL_CONCURRENCY = 4
_KNOWN_SECTIONS = {
    "스크럼": "scrum",
    "새로 배운 내용": "learned",
    "오늘의 도전 과제와 해결 방법": "challenges",
    "오늘의 회고": "retrospective",
    "참고 자료 및 링크": "references",
}


class AgentState(TypedDict, total=False):
    user_id: str
    page_url: str
    content: Dict[str, Any]
    til: Dict[str, Any]
    json_path: str
    markdown_path: str


def build_notion_agent(fetch_page: FetchPage, artifact_dir: Path):
    async def fetch(state: AgentState) -> AgentState:
        user_id = state.get("user_id", "").strip()
        page_url = state.get("page_url", "").strip()
        if not user_id or not page_url:
            raise ValueError("user_id and page_url are required")
        return {"content": await fetch_page(user_id, page_url)}

    def write(state: AgentState) -> AgentState:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256(
            f"{state['user_id']}\0{state['page_url']}".encode()
        ).hexdigest()[:24]
        til = parse_til_detail(_page_text(state["content"]))
        payload = {
            "source": "notion_mcp",
            "user_id": state["user_id"],
            "page_url": state["page_url"],
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "content": state["content"],
            "til": til,
        }
        json_path = artifact_dir / f"{key}.json"
        markdown_path = artifact_dir / f"{key}.md"
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        markdown_path.write_text(_markdown(state["content"], til))
        return {"json_path": str(json_path), "markdown_path": str(markdown_path)}

    graph = StateGraph(AgentState)
    graph.add_node("fetch", fetch)
    graph.add_node("write", write)
    graph.add_edge(START, "fetch")
    graph.add_edge("fetch", "write")
    graph.add_edge("write", END)
    return graph.compile()


def parse_til_detail(markdown: str) -> Dict[str, Any]:
    """Parse a TIL detail page without source-location metadata."""
    return _parse_til_detail(markdown)


def _parse_til_detail(markdown: str, source_path: str | None = None) -> Dict[str, Any]:
    """Parse TIL headings and optionally retain line evidence for local files."""
    result = {
        "date": None,
        "scrum": [],
        "learned": [],
        "challenges": [],
        "retrospective": [],
        "references": [],
        "unparsed_sections": [],
    }
    evidence = {
        "scrum": [],
        "learned": [],
        "challenges": [],
        "retrospective": [],
    }
    section = None
    learned_active = False
    topic = None
    challenge = None
    challenge_indent = None

    def line_evidence(claim: str, raw_line: str, line_number: int, section_name: str):
        return {
            "claim": claim,
            "source_path": source_path,
            "line_start": line_number,
            "line_end": line_number,
            "exact_quote": raw_line,
            "section": section_name,
        }

    def add_topic(raw_heading: str, line_number: int):
        nonlocal topic
        if re.search(r"[:：]", raw_heading):
            topic_name, description = re.split(r"[:：]", raw_heading, maxsplit=1)
            description = description.strip() or None
        else:
            topic_name, description = raw_heading, None
        topic = {
            "topic": topic_name.strip(),
            "description": description,
            "details": [],
        }
        result["learned"].append(topic)
        evidence["learned"].append({
            "topic": line_evidence(
                description or topic["topic"],
                raw_line,
                line_number,
                "📚 What I Learned",
            )
        })

    for line_number, raw_line in enumerate(_normalise_markdown(markdown).splitlines(), 1):
        heading = re.match(r"^\s*#{1,3}\s+(?!#)(.+?)\s*$", raw_line)
        if heading:
            date = re.fullmatch(r"날짜\s*[:：]\s*(.+)", heading.group(1).strip())
            if date:
                result["date"] = date.group(1).strip() or None
                continue
            if raw_line.lstrip().startswith("###"):
                section_name = heading.group(1).strip()
                if learned_active and re.fullmatch(
                    r"주제\s*\d+\s*[:：].+", section_name
                ):
                    add_topic(section_name, line_number)
                    continue
                section = _KNOWN_SECTIONS.get(section_name)
                if section is None:
                    result["unparsed_sections"].append(section_name)
                else:
                    learned_active = section == "learned"
                topic = None
                challenge = None
                challenge_indent = None
            continue

        if re.match(r"^\s*###(?!#)\S", raw_line):
            section = None
            learned_active = False
            topic = None
            challenge = None
            challenge_indent = None
            continue

        if section == "learned":
            heading = re.match(r"^\s*####\s+(?!#)(.+?)\s*$", raw_line)
            if heading:
                add_topic(heading.group(1).strip(), line_number)
                continue

        bullet = re.match(r"^(\s*)[-*]\s+(.+?)\s*$", raw_line)
        if bullet:
            indent = len(bullet.group(1).expandtabs(4))
            value = bullet.group(2).strip()
            if section == "scrum":
                result["scrum"].append(value)
                evidence["scrum"].append(
                    line_evidence(value, raw_line, line_number, "📈 Growth")
                )
            elif section == "learned" and topic is not None:
                topic["details"].append(value)
            elif section == "challenges":
                optional = re.match(r"^(프로젝트|결과)\s*[:：]\s*(.*)$", value)
                if optional:
                    if challenge is not None and indent > challenge_indent:
                        challenge["project" if optional.group(1) == "프로젝트" else "result"] = (
                            optional.group(2).strip() or None
                        )
                    continue
                solution = re.match(r"^해결 방법\s*[:：]\s*(.*)$", value)
                if solution:
                    if challenge is not None:
                        challenge["solution"] = solution.group(1).strip() or None
                        evidence["challenges"][-1]["solution"] = line_evidence(
                            challenge["solution"] or value,
                            raw_line,
                            line_number,
                            "🔥 Technical Challenges",
                        )
                    continue
                if challenge_indent is None or indent <= challenge_indent:
                    title = (
                        re.split(r"[:：]", value, maxsplit=1)[1].strip()
                        if re.search(r"[:：]", value)
                        else value
                    )
                    challenge = {
                        "title": title,
                        "project": None,
                        "result": None,
                        "solution": None,
                    }
                    result["challenges"].append(challenge)
                    evidence["challenges"].append({
                        "challenge": line_evidence(
                            title,
                            raw_line,
                            line_number,
                            "🔥 Technical Challenges",
                        ),
                        "solution": None,
                    })
                    challenge_indent = indent
            elif section == "retrospective":
                result["retrospective"].append(value)
                evidence["retrospective"].append(
                    line_evidence(value, raw_line, line_number, "🎯 Retrospective")
                )
            elif section == "references":
                link = re.fullmatch(r"\[([^\]]+)\]\(([^)]+)\)(?:\s+(.+))?", value)
                title = link.group(1) if link else value
                if link and link.group(3):
                    title = f"{title} {link.group(3).strip()}"
                result["references"].append(
                    {"title": title, "url": link.group(2)}
                    if link
                    else {"title": value, "url": None}
                )

    if source_path is not None:
        result["_evidence"] = evidence
    return result


async def collect_til(
    fetch_page: FetchPage,
    user_id: str,
    index_url: str,
    limit: int | None = None,
) -> Dict[str, Any]:
    if limit is not None and limit < 0:
        raise ValueError("limit must be non-negative")
    index = await fetch_page(user_id, index_url)
    entries = _parse_til_index(_page_text(index))
    if limit is not None:
        entries = entries[:limit]
    semaphore = asyncio.Semaphore(_DETAIL_CONCURRENCY)

    async def fetch_detail(detail_url: str):
        async with semaphore:
            try:
                page = await fetch_page(user_id, detail_url)
                detail = parse_til_detail(_page_text(page))
            except Exception as error:
                return None, {
                    "url": detail_url,
                    "error": str(error),
                }
        return detail, None

    tasks = {}
    for entry in entries:
        detail_url = entry["detail_url"]
        if detail_url not in tasks:
            tasks[detail_url] = asyncio.create_task(fetch_detail(detail_url))
    details = dict(zip(tasks, await asyncio.gather(*tasks.values())))
    fetched = []
    for entry in entries:
        detail, error = details[entry["detail_url"]]
        fetched.append(({**entry, "detail": detail}, error))
    return {
        "entries": [entry for entry, _ in fetched],
        "errors": [error for _, error in fetched if error is not None],
    }


def _parse_til_index(markdown: str, source_path: str | None = None) -> list[Dict[str, Any]]:
    entries = []
    week = None
    for line_number, raw_line in enumerate(_normalise_markdown(markdown).splitlines(), 1):
        heading = re.match(r"^\s*#{2,3}\s+(?!#)(.+?)\s*$", raw_line)
        if heading:
            week_text = heading.group(1).strip()
            match = re.fullmatch(
                r"\[([^\]]+)\](?:\s*[:：]\s*(\S.*))?", week_text
            )
            if match:
                week = {
                    "week_label": match.group(1),
                    "week_topic": match.group(2),
                    "week_topics": [],
                }
                if source_path is not None:
                    week["_week_topic_evidence"] = []
            else:
                week = None
            continue
        if week is None:
            continue
        bullet = re.match(r"^\s*[-*]\s+(.+?)\s*$", raw_line)
        content = bullet.group(1).strip() if bullet else raw_line.strip()
        link = _index_link(content)
        if link is None:
            if bullet:
                week["week_topics"].append(content)
                if source_path is not None:
                    week["_week_topic_evidence"].append({
                        "claim": content,
                        "source_path": source_path,
                        "line_start": line_number,
                        "line_end": line_number,
                        "exact_quote": raw_line,
                        "section": "🛠 Tech Stack",
                    })
            continue
        date_title = re.match(
            r"^(\d{2}\.\d{2}\.\d{2}|\d{4}-\d{2}-\d{2})(?:\s+(.*?))?\s*$",
            content[:link["start"]].strip(),
        )
        if date_title is None:
            continue
        title = (date_title.group(2) or "").strip() or link["label"]
        entries.append({
            **week,
            "date": date_title.group(1),
            "title": title,
            "detail_url": link["url"],
        })
    return entries


def _index_link(value: str) -> Dict[str, str | int] | None:
    patterns = (
        r"\(\s*\[([^\]]+)\]\(([^)]+)\)\s*\)(?:\s+.*)?$",
        r"\[([^\]]+)\]\(([^)]+)\)(?:\s+.*)?$",
        r"\(\s*([^()\s]+)\s*\)(?:\s+.*)?$",
    )
    for pattern in patterns:
        match = re.search(pattern, value)
        if match:
            return {
                "label": match.group(1) if len(match.groups()) == 2 else "",
                "url": match.group(2) if len(match.groups()) == 2 else match.group(1),
                "start": match.start(),
            }
    return None


def load_til_corpus(root: Path) -> Dict[str, Any]:
    """Load a local ``README.md`` index and its dated daily Markdown files."""
    root = Path(root).resolve()
    errors = []
    index_path = root / "README.md"
    if not index_path.is_file():
        return {
            "_corpus_root": str(root),
            "entries": [],
            "errors": [{"path": str(index_path), "error": "README.md not found"}],
        }

    entries = _parse_til_index(index_path.read_text(), str(index_path))
    source_urls = {
        _normalise_index_date(entry["date"]): entry.get("detail_url")
        for entry in entries
        if entry.get("detail_url")
    }
    files = {
        path.stem: path
        for path in root.rglob("*.md")
        if path.name not in {"README.md", "template.md"}
    }
    loaded = []
    for entry in entries:
        date = _normalise_index_date(entry["date"])
        path = files.get(date)
        if path is None:
            error = {"date": entry["date"], "error": "daily TIL file not found"}
            errors.append(error)
            loaded.append({**entry, "source_url": source_urls.get(date), "detail": None})
            continue
        try:
            detail = _parse_til_detail(path.read_text(), str(path))
        except Exception as error:
            errors.append({"path": str(path), "error": str(error)})
            loaded.append({**entry, "source_url": source_urls.get(date), "detail": None})
            continue
        if detail["date"] is None:
            detail["date"] = date
        loaded.append({**entry, "source_url": source_urls.get(date), "detail": detail})
    return {"_corpus_root": str(root), "entries": loaded, "errors": errors}


def _normalise_index_date(value: str) -> str:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return value
    match = re.fullmatch(r"(\d{2})\.(\d{2})\.(\d{2})", value)
    if match:
        return f"20{match.group(1)}-{match.group(2)}-{match.group(3)}"
    return value


def _page_text(content: Any) -> str:
    if isinstance(content, str):
        text = content
    elif isinstance(content, dict):
        items = content.get("content")
        if isinstance(items, list):
            parts = []
            for item in items:
                raw_text = (
                    item
                    if isinstance(item, str)
                    else item.get("text")
                    if isinstance(item, dict) and item.get("type") == "text"
                    else None
                )
                if not isinstance(raw_text, str):
                    continue
                try:
                    parsed = json.loads(raw_text)
                except (TypeError, ValueError):
                    parsed = None
                parts.append(
                    parsed["text"]
                    if isinstance(parsed, dict) and isinstance(parsed.get("text"), str)
                    else raw_text
                )
            text = "\n".join(parts)
        elif isinstance(content.get("text"), str):
            text = content["text"]
        else:
            return ""
    else:
        return ""

    matches = re.findall(r"<content>(.*?)</content>", text, flags=re.DOTALL)
    return "\n".join(matches) if matches else text


def _normalise_markdown(markdown: Any) -> str:
    if not isinstance(markdown, str):
        return ""
    text = html.unescape(markdown)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(
        r"</?(?:column|table|tbody|thead|tfoot|tr|th|td)\b[^>]*>",
        "\n",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\\([\\`*_\[\]()#+.!-])", r"\1", text)


def _markdown(content: Dict[str, Any], til: Dict[str, Any]) -> str:
    title = content.get("title")
    body = _normalise_markdown(_page_text(content)).strip()
    if body:
        prefix = f"# {title}\n\n" if isinstance(title, str) and title else ""
        return prefix + body + "\n"
    return "# Notion MCP export\n\n```json\n" + json.dumps(
        til or content, ensure_ascii=False, indent=2
    ) + "\n```\n"
