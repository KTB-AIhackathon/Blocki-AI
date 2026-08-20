"""The Developer TIL Dashboard tree, loaded from the markdown next to this file.

The public original is
https://sedate-faucet-61e.notion.site/Developer-TIL-Dashboard-6f816da5521c83ed8d89012fa47f4035
`templates/*.md` is a transcription of that tree. `ensure_dashboard` creates
those files under the user's private root when the named page is missing.

Three strings must never drift:

- the dashboard title, which is how we find the page again on the next job
- `ARCHIVE_TITLE`, which is where generated documents land and which the
  collector skips so we never read our own output back as evidence
- the `##` headings inside the daily template, which are how the agent locates
  the GitHub evidence block without touching what the human wrote
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_DIR = Path(__file__).parent / "templates"

DASHBOARD_TITLE = "Developer TIL Dashboard"
DASHBOARD_ICON = "🧑‍💻"

DAILY_TEMPLATE_TITLE = "일일 Developer TIL — Portfolio Ready 템플릿"

#: 생성된 포트폴리오·이력서가 쌓이는 페이지. 대시보드 직속이 아니라 이 아래로 들어간다.
#: `collect.notion_til` 도 같은 제목으로 이 하위 트리를 건너뛴다 — 계층 규칙상 이 모듈을
#: 가져올 수 없어 그쪽에 문자열이 한 번 더 있다.
ARCHIVE_TITLE = "생성된 포트폴리오 및 이력서"

#: The heading the agent fills in on a daily TIL. Everything else belongs to
#: the human. Second-stage work keys off this exact string.
EVIDENCE_HEADING = "## 💻 GitHub 작업 근거"


def _read(name: str) -> str:
    return (_DIR / name).read_text(encoding="utf-8")


DASHBOARD_BODY = _read("dashboard.md")
DAILY_TEMPLATE_BODY = _read("daily.md")
ARCHIVE_BODY = _read("archive.md")


def is_dashboard_title(title: str | None) -> bool:
    """True for the exact dashboard name, with or without the icon prefix."""
    cleaned = (title or "").replace(DASHBOARD_ICON, "").strip()
    return cleaned == DASHBOARD_TITLE


@dataclass(frozen=True)
class TemplatePage:
    """One page to create under the dashboard, in the order listed."""

    title: str
    icon: str
    body: str


ARCHIVE_PAGE = TemplatePage(ARCHIVE_TITLE, "📂", ARCHIVE_BODY)

CHILD_PAGES: tuple[TemplatePage, ...] = (
    TemplatePage(DAILY_TEMPLATE_TITLE, "📗", DAILY_TEMPLATE_BODY),
    ARCHIVE_PAGE,
)


def render_dashboard_body(urls: dict[str, str] | None = None) -> str:
    """Put created child page mentions into the dashboard markdown.

    `{{page:제목}}` becomes a Notion `<page url="...">` tag when we have a URL,
    otherwise the title alone. The human later edits this page; we only render
    it when we first build the tree.
    """
    body = DASHBOARD_BODY
    for title, url in (urls or {}).items():
        token = "{{page:" + title + "}}"
        body = body.replace(
            token, f'<page url="{url}">{title}</page>' if url else title
        )
    for child in CHILD_PAGES:
        token = "{{page:" + child.title + "}}"
        if token in body:
            body = body.replace(token, child.title)
    return body
