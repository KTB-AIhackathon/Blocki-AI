from pathlib import Path

from fastapi.testclient import TestClient

from app.contracts import JobRequest, JobResult
from app.main import create_app

ROOT = Path(__file__).resolve().parents[1]


def test_notion_module_is_gone() -> None:
    assert not (ROOT / "app" / "notion").exists()


def test_contracts_have_no_notion_surface() -> None:
    assert "notion" not in JobRequest.model_fields
    assert "notion" not in JobResult.model_fields
    source = (ROOT / "app" / "contracts.py").read_text(encoding="utf-8")
    assert "X-Notion-Token" not in source
    assert "NotionWriteResult" not in source


def test_jobs_ignore_notion_header(monkeypatch) -> None:
    monkeypatch.setenv("INTERNAL_API_KEY", "dev-internal-key")
    client = TestClient(create_app())
    response = client.post(
        "/internal/jobs",
        json={"job_id": "j1", "user_id": "u1", "job_type": "progress_summary"},
        headers={
            "X-Internal-Key": "dev-internal-key",
            "X-Notion-Token": "secret-should-not-be-used",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert "notion" not in body
    assert "secret-should-not-be-used" not in response.text
    assert body["error"]["code"] == "missing_pat"
