from __future__ import annotations

from pathlib import Path

PLACEHOLDERS = (
    "name",
    "contact_md",
    "experience_md",
    "education_md",
    "summary_md",
    "skills_md",
    "projects_md",
)

TEMPLATES_ROOT = Path(__file__).resolve().parent.parent / "templates"


def template_path(kind: str, version: str) -> Path:
    return TEMPLATES_ROOT / kind / f"{version}.md"


def load_template(kind: str, version: str) -> str:
    path = template_path(kind, version)
    return path.read_text(encoding="utf-8")


def render_template(text: str, values: dict[str, str]) -> str:
    out = text
    for key in PLACEHOLDERS:
        out = out.replace("{{" + key + "}}", values.get(key, ""))
    return out
