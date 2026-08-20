"""워커가 운영에서 열린 채로 뜨지 않는지 본다.

Spring 쪽에도 같은 검사가 있지만, 워커는 GitHub PAT을 받아 쓰는 쪽이라
설정 하나가 빠지면 그대로 남의 저장소를 읽고 PR을 여는 통로가 된다.
"""

from __future__ import annotations

import pytest

from app.startup import BLOCKI_ENV, DEV_KEYS, verify_environment

REAL_KEY = "a3f19c74e2b85d06"


def env(**overrides: str | None) -> dict[str, str]:
    base = {BLOCKI_ENV: "prod", "INTERNAL_API_KEY": REAL_KEY}
    base.update({k: v for k, v in overrides.items() if v is not None})
    for key, value in overrides.items():
        if value is None:
            base.pop(key, None)
    return base


def test_development_default_stops_the_worker() -> None:
    for key in DEV_KEYS:
        with pytest.raises(RuntimeError, match="INTERNAL_API_KEY"):
            verify_environment(env(INTERNAL_API_KEY=key))


def test_a_missing_internal_key_stops_the_worker() -> None:
    with pytest.raises(RuntimeError, match="INTERNAL_API_KEY"):
        verify_environment(env(INTERNAL_API_KEY=None))


def test_a_short_internal_key_stops_the_worker() -> None:
    with pytest.raises(RuntimeError, match="INTERNAL_API_KEY"):
        verify_environment(env(INTERNAL_API_KEY="short"))


def test_a_stub_mcp_url_stops_the_worker() -> None:
    """스텁을 가리킨 채 배포되면 문서가 조용히 가짜 데이터로 만들어진다."""
    with pytest.raises(RuntimeError, match="GITHUB_MCP_URL"):
        verify_environment(env(GITHUB_MCP_URL="http://github-mcp:18765/mcp"))
    with pytest.raises(RuntimeError, match="NOTION_MCP_URL"):
        verify_environment(env(NOTION_MCP_URL="http://localhost:18766/mcp"))


def test_the_real_mcp_urls_pass() -> None:
    verify_environment(env(
        GITHUB_MCP_URL="https://api.githubcopilot.com/mcp/",
        NOTION_MCP_URL="https://mcp.notion.com/mcp",
    ))


def test_omitting_the_mcp_urls_passes_because_the_defaults_are_real() -> None:
    verify_environment(env())


def test_every_problem_is_named_at_once() -> None:
    with pytest.raises(RuntimeError) as caught:
        verify_environment({
            BLOCKI_ENV: "prod",
            "INTERNAL_API_KEY": "local-internal-key",
            "GITHUB_MCP_URL": "http://github-mcp:18765/mcp",
        })

    message = str(caught.value)
    assert "INTERNAL_API_KEY" in message
    assert "GITHUB_MCP_URL" in message


def test_local_runs_are_left_alone() -> None:
    verify_environment({
        "INTERNAL_API_KEY": "local-internal-key",
        "GITHUB_MCP_URL": "http://github-mcp:18765/mcp",
    })
    verify_environment({BLOCKI_ENV: "local", "INTERNAL_API_KEY": "local-internal-key"})


def test_the_message_does_not_echo_the_key() -> None:
    with pytest.raises(RuntimeError) as caught:
        verify_environment(env(INTERNAL_API_KEY="local-internal-key"))

    assert "local-internal-key" not in str(caught.value)
