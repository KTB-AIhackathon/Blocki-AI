from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"
FORBIDDEN = ("sqlite3", "sqlalchemy", "psycopg", "asyncpg", "django.db")


def test_fastapi_app_has_no_database_imports() -> None:
    hits: list[str] = []
    for path in APP.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in FORBIDDEN:
            if needle in text:
                hits.append(f"{path.name}:{needle}")
    assert hits == []


def test_fastapi_dependencies_have_no_database() -> None:
    text = PYPROJECT.read_text(encoding="utf-8")
    for needle in ("sqlite", "sqlalchemy", "psycopg", "asyncpg"):
        assert needle not in text.lower()
