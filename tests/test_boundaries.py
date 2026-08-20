"""Architecture rules, enforced as tests.

The folder split only pays off if the arrows stay one-way. These read imports
rather than trusting review.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"
PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"

# package -> packages it must never import
FORBIDDEN_IMPORTS: dict[str, tuple[str, ...]] = {
    "collect": ("app.analyze", "app.pipelines", "app.publish", "app.api", "app.llm"),
    "analyze": ("app.collect", "app.pipelines", "app.publish", "app.api"),
    "pipelines": ("app.collect", "app.publish", "app.api"),
    "publish": ("app.collect", "app.analyze", "app.pipelines", "app.api"),
    "execute": ("app.collect", "app.analyze", "app.pipelines", "app.publish", "app.api"),
    "llm": ("app.collect", "app.pipelines", "app.publish", "app.api"),
    "contracts": ("app.collect", "app.analyze", "app.pipelines", "app.publish", "app.api"),
}

DB_DRIVERS = ("sqlite3", "sqlalchemy", "psycopg", "asyncpg", "django.db")

# Spring owns the credential vault. The Notion prototype kept OAuth tokens in
# `work/notion-mcp-tokens.json`; if that ever comes back, this worker stops
# being stateless and starts being a target.
TOKEN_VAULT = ("TokenStorage", "OAuthClientProvider", "tokens.json", "client_secret", "refresh_token")


def imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


@pytest.mark.parametrize("package,banned", sorted(FORBIDDEN_IMPORTS.items()))
def test_layer_does_not_import_upward(package: str, banned: tuple[str, ...]) -> None:
    offences: list[str] = []
    for path in sorted((APP / package).rglob("*.py")):
        for module in imports_of(path):
            for forbidden in banned:
                if module == forbidden or module.startswith(forbidden + "."):
                    offences.append(f"{path.relative_to(APP)} -> {module}")
    assert offences == []


def test_only_api_layer_wires_secrets_to_layers() -> None:
    """PAT and Notion token headers are read in exactly one place each."""
    readers = {
        path.relative_to(APP).as_posix()
        for path in APP.rglob("*.py")
        if "Header(alias=" in path.read_text(encoding="utf-8")
    }
    assert readers == {"api/deps.py"}


def test_no_credential_vault_anywhere() -> None:
    """Tokens arrive as headers, live in a closure, and are never written down."""
    hits = [
        f"{path.relative_to(APP)}:{needle}"
        for path in APP.rglob("*.py")
        for needle in TOKEN_VAULT
        if needle in path.read_text(encoding="utf-8")
    ]
    assert hits == []


def test_no_database_driver_anywhere() -> None:
    hits = [
        f"{path.relative_to(APP)}:{needle}"
        for path in APP.rglob("*.py")
        for needle in DB_DRIVERS
        if needle in path.read_text(encoding="utf-8")
    ]
    assert hits == []
    lowered = PYPROJECT.read_text(encoding="utf-8").lower()
    assert not any(n in lowered for n in ("sqlite", "sqlalchemy", "psycopg", "asyncpg"))


def test_pipelines_registry_covers_every_job_type() -> None:
    from app import pipelines
    from app.contracts import LEGACY_DOCUMENT_JOB_TYPE
    from app.contracts.job import JobType
    from typing import get_args

    declared = set(get_args(JobType)) - {LEGACY_DOCUMENT_JOB_TYPE}
    assert declared == set(pipelines.REGISTRY)


def test_every_document_pipeline_has_a_template() -> None:
    from app import pipelines, render

    for name, pipeline in pipelines.REGISTRY.items():
        if pipeline.requires != "document":
            continue
        assert render.template_path(pipeline.kind, "v1").is_file(), name
