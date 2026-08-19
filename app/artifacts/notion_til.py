"""LangGraph workflow for user-scoped Notion MCP exports."""

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, TypedDict

from langgraph.graph import END, START, StateGraph


FetchPage = Callable[[str, str], Awaitable[Dict[str, Any]]]


class AgentState(TypedDict, total=False):
    user_id: str
    page_url: str
    content: Dict[str, Any]
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
        payload = {
            "source": "notion_mcp",
            "user_id": state["user_id"],
            "page_url": state["page_url"],
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "content": state["content"],
            "tasks": _tasks(state["content"].get("text", "")),
        }
        json_path = artifact_dir / f"{key}.json"
        markdown_path = artifact_dir / f"{key}.md"
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        markdown_path.write_text(_markdown(state["content"], payload["tasks"]))
        return {"json_path": str(json_path), "markdown_path": str(markdown_path)}

    graph = StateGraph(AgentState)
    graph.add_node("fetch", fetch)
    graph.add_node("write", write)
    graph.add_edge(START, "fetch")
    graph.add_edge("fetch", "write")
    graph.add_edge("write", END)
    return graph.compile()


def _tasks(text: Any) -> list[Dict[str, Any]]:
    if not isinstance(text, str):
        return []
    tasks = []
    day = None
    base_indent = None
    parent = None
    for line in text.splitlines():
        stripped = line.lstrip()
        heading = re.fullmatch(r"##\s+(.+)", stripped)
        if heading:
            day = heading.group(1).strip()
            base_indent = None
            parent = None
            continue
        match = re.fullmatch(r"-\s+\[([ xX])\]\s+(.+)", stripped)
        if not match:
            continue
        indent = len(line) - len(stripped)
        if base_indent is None or indent < base_indent:
            base_indent = indent
        title = re.sub(r'\s+\{color="[^"]+"\}$', "", match.group(2)).strip()
        task_parent = parent if indent > base_indent else None
        if task_parent is None:
            parent = title
        tasks.append(
            {
                "day": day,
                "title": title,
                "completed": match.group(1).lower() == "x",
                "parent": task_parent,
            }
        )
    return tasks


def _markdown(content: Dict[str, Any], tasks: list[Dict[str, Any]]) -> str:
    title = content.get("title")
    if isinstance(title, str) and tasks:
        lines = [f"# {title}", ""]
        current_day = object()
        for task in tasks:
            if task["day"] != current_day:
                current_day = task["day"]
                if lines[-1]:
                    lines.append("")
                lines.extend([f"## {current_day or '기타'}", ""])
            prefix = "  " if task["parent"] else ""
            check = "x" if task["completed"] else " "
            lines.append(f"{prefix}- [{check}] {task['title']}")
        return "\n".join(lines) + "\n"
    return "# Notion MCP export\n\n```json\n" + json.dumps(
        content, ensure_ascii=False, indent=2
    ) + "\n```\n"
