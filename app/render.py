"""Markdown templating.

Allowlist substitution only — no Jinja, no conditionals, no expressions, so a
repository description can never become template code. Sections whose
placeholder resolved to nothing are removed rather than left as empty
headings, because a portfolio with a blank "Projects" heading reads worse than
one without the heading.
"""

from __future__ import annotations

import re
from hashlib import sha256
from pathlib import Path

from app.contracts import TemplateRef

PLACEHOLDERS = (
    "name",
    "contact_md",
    "summary_md",
    "stats_md",
    "skills_md",
    "projects_md",
    "learning_md",
    "experience_md",
    "education_md",
    "selection_md",
)

TEMPLATES_ROOT = Path(__file__).resolve().parents[1] / "templates"

_HEADING_RE = re.compile(r"^(#{1,6})\s*(.*)$")
_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")
_RULE = "---"


def template_path(kind: str, version: str) -> Path:
    return TEMPLATES_ROOT / kind / f"{version}.md"


def load_template(kind: str, version: str) -> str:
    return template_path(kind, version).read_text(encoding="utf-8")


def template_ref(kind: str, version: str) -> TemplateRef:
    digest = sha256(template_path(kind, version).read_bytes()).hexdigest()
    return TemplateRef(kind=kind, version=version, sha256=digest)


def render(kind: str, version: str, values: dict[str, str]) -> str:
    return prune_empty_sections(substitute(load_template(kind, version), values))


def substitute(text: str, values: dict[str, str]) -> str:
    """One pass, so a substituted value can never consume a later placeholder."""

    def swap(match: re.Match[str]) -> str:
        key = match.group(1)
        return (values.get(key) or "").strip() if key in PLACEHOLDERS else match.group(0)

    return _PLACEHOLDER_RE.sub(swap, text)


def prune_empty_sections(markdown: str) -> str:
    preamble, blocks = _split(markdown)
    keep = _survivors(blocks)
    parts: list[str] = []
    top_sections = 0
    if preamble.strip():
        parts.append(preamble.strip())
    for (level, heading, body), alive in zip(blocks, keep):
        if not alive:
            continue
        if level == 2:
            if top_sections:
                parts.append(_RULE)
            top_sections += 1
        section = "\n".join([heading, "", _clean(body)]).rstrip()
        parts.append(section)
    text = "\n\n".join(p for p in parts if p.strip())
    text = re.sub(r"\n{3,}", "\n\n", text).rstrip()
    while text.endswith(_RULE):
        text = text[: -len(_RULE)].rstrip()
    return text + "\n" if text else ""


def _split(markdown: str) -> tuple[str, list[tuple[int, str, list[str]]]]:
    preamble: list[str] = []
    blocks: list[tuple[int, str, list[str]]] = []
    for line in markdown.splitlines():
        match = _HEADING_RE.match(line)
        if match:
            hashes, title = match.groups()
            blocks.append((len(hashes), f"{hashes} {title.strip()}".rstrip(), []))
        elif blocks:
            blocks[-1][2].append(line)
        else:
            preamble.append(line)
    return "\n".join(preamble), blocks


def _survivors(blocks: list[tuple[int, str, list[str]]]) -> list[bool]:
    keep = [False] * len(blocks)
    for index in range(len(blocks) - 1, -1, -1):
        level, _, body = blocks[index]
        has_body = any(line.strip() and line.strip() != _RULE for line in body)
        has_child = False
        for other in range(index + 1, len(blocks)):
            if blocks[other][0] <= level:
                break
            if keep[other]:
                has_child = True
                break
        keep[index] = has_body or has_child
    return keep


def _clean(body: list[str]) -> str:
    text = "\n".join(body).strip()
    while text.endswith(_RULE):
        text = text[: -len(_RULE)].rstrip()
    return text
