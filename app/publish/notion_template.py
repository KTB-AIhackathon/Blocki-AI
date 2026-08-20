"""The Developer TIL Dashboard tree, loaded from the markdown next to this file.

The public original is
https://sedate-faucet-61e.notion.site/Developer-TIL-Dashboard-6f816da5521c83ed8d89012fa47f4035
`templates/*.md` is a transcription of that tree. `ensure_dashboard` creates
those files under the user's private root when the named page is missing.

Two strings must never drift:

- the dashboard title, which is how we find the page again on the next job
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
PORTFOLIO_TITLE = "developer-portfolio-3-projects"

#: The heading the agent fills in on a daily TIL. Everything else belongs to
#: the human. Second-stage work keys off this exact string.
EVIDENCE_HEADING = "## 💻 GitHub 작업 근거"

EXAMPLE_TITLES = (
    "2026-08-20 · [예시] 배포 지표 개선",
    "2026-08-19 · [예시] ArgoCD 설정",
    "2026-08-18 · [예시] HPA 적용 실험",
)


def _read(name: str) -> str:
    return (_DIR / name).read_text(encoding="utf-8")


DASHBOARD_BODY = _read("dashboard.md")
DAILY_TEMPLATE_BODY = _read("daily.md")
PORTFOLIO_BODY = _read("portfolio.md")
_EXAMPLE_BANNER = _read("example-banner.md")


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


CHILD_PAGES: tuple[TemplatePage, ...] = (
    TemplatePage(DAILY_TEMPLATE_TITLE, "📗", DAILY_TEMPLATE_BODY),
    *(
        TemplatePage(title, "📝", _EXAMPLE_BANNER + DAILY_TEMPLATE_BODY)
        for title in EXAMPLE_TITLES
    ),
    TemplatePage(PORTFOLIO_TITLE, "💼", PORTFOLIO_BODY),
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
