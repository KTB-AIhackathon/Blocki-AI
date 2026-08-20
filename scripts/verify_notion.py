"""Prove the Notion write path against a real server.

    NOTION_TOKEN=ntn_... NOTION_PARENT_ID=<page id> python scripts/verify_notion.py

Notion hosts the MCP server and owns the tool schema, so the only honest way to
know our adapter still fits is to ask the live server. Three steps:

  1. list the tools and print the create-page schema
  2. show the arguments our adapter builds from that schema (nothing sent yet)
  3. with --write, create a page from a markdown sample that exercises the
     formatting we actually emit, read it back, and diff it

Add --write only against a scratch page. Step 3 creates a real page.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone

from app.publish.notion import kst_today, publish_markdown
from app.publish.notion_mcp import CREATE_TOOLS, notion_mcp_url, open_session
from app.publish.notion_schema import create_page_arguments

# Every markdown feature the templates emit. If Notion mangles one of these the
# generated portfolio arrives broken, so the readback checks for all of them.
SAMPLE = f"""# Blocki 검증 {kst_today().isoformat()}

## 🚀 Projects

- **acme/demo** · Python · 커밋 12개
- [ ] 미완료 항목
- [x] 완료 항목

| 종류 | ID | 시각 |
| --- | --- | --- |
| commit | `abc1234` | 20:00 |

```python
print("fenced code survives")
```
"""

MARKERS = ("🚀 Projects", "acme/demo", "미완료 항목", "abc1234", "fenced code survives")


async def main() -> int:
    token = (os.environ.get("NOTION_TOKEN") or "").strip()
    if not token:
        print("NOTION_TOKEN required", file=sys.stderr)
        return 1
    parent = (os.environ.get("NOTION_PARENT_ID") or "").strip() or None
    write = "--write" in sys.argv

    report: dict = {
        "server": notion_mcp_url(),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "parent_id": parent,
        "wrote": False,
    }

    try:
        session = await open_session(token)
    except Exception as exc:
        print(f"connect failed: {_scrub(exc, token)}", file=sys.stderr)
        return 1

    report["tools"] = sorted(session.tools)
    name = next((n for n in CREATE_TOOLS if n in session.tools), None)
    if name is None:
        print(f"no create tool; server offers {report['tools']}", file=sys.stderr)
        return 1
    report["create_tool"] = name

    schema = getattr(session.tools[name], "args_schema", None)
    report["schema"] = schema
    try:
        report["arguments"] = create_page_arguments(
            schema, title="Blocki 검증", markdown=SAMPLE, parent_id=parent
        )
    except Exception as exc:
        report["arguments_error"] = str(exc)
        _emit(report)
        print("adapter cannot fill the live schema; see schema above", file=sys.stderr)
        return 1

    if not write:
        _emit(report)
        print("dry run ok. add --write to create a real page.")
        return 0

    result = await publish_markdown(
        title=f"Blocki 검증 {kst_today().isoformat()}",
        markdown=SAMPLE,
        notion_token=token,
        parent_id=parent,
        session=session,
    )
    report["wrote"] = True
    report["write"] = result.model_dump(mode="json")
    if not result.ok or not result.page_id:
        _emit(report)
        print("write failed", file=sys.stderr)
        return 1

    try:
        raw = await session.read_page(result.page_url or result.page_id)
    except Exception as exc:
        report["readback_error"] = _scrub(exc, token)
        _emit(report)
        return 1

    text = json.dumps(raw, ensure_ascii=False, default=str)
    missing = [marker for marker in MARKERS if marker not in text]
    report["readback_missing"] = missing
    report["fidelity"] = "exact" if not missing else "lossy"
    _emit(report)
    if missing:
        print(f"readback lost: {missing}", file=sys.stderr)
        return 1
    print(f"ok. page={result.page_url}")
    return 0


def _emit(report: dict) -> None:
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


def _scrub(exc: BaseException, token: str) -> str:
    return str(exc).replace(token, "«token»")[:400]


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
