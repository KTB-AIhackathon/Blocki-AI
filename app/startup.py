"""Boot-time refusal to run production with a development configuration.

The worker holds no database and no vault, so the only thing standing between
the internet and someone else's GitHub account is the internal key and the MCP
endpoints it was pointed at. Both are environment variables, and a deploy that
forgets one looks perfectly healthy: the service answers, the pipelines run, and
the documents come out of a stub. That is worth refusing to start over.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

BLOCKI_ENV = "BLOCKI_ENV"
PRODUCTION = ("prod", "production")

# Anything published in this repository or in the compose files.
DEV_KEYS = frozenset({
    "local-internal-key",
    "dev-internal-key",
    "local-ops-key",
    "change-me",
    "changeme",
    "secret",
})
MIN_KEY_LENGTH = 16

REAL_GITHUB_MCP = "api.githubcopilot.com"
REAL_NOTION_MCP = "mcp.notion.com"


def is_production(env: Mapping[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    return (source.get(BLOCKI_ENV) or "").strip().lower() in PRODUCTION


def verify_environment(env: Mapping[str, str] | None = None) -> None:
    source = os.environ if env is None else env
    if not is_production(source):
        return
    problems = _problems(source)
    if problems:
        raise RuntimeError(
            f"{BLOCKI_ENV}=prod 인데 개발용 설정이 남아 있습니다:\n  - "
            + "\n  - ".join(problems)
        )


def _problems(env: Mapping[str, str]) -> list[str]:
    problems: list[str] = []

    key = (env.get("INTERNAL_API_KEY") or "").strip()
    if not key:
        problems.append("INTERNAL_API_KEY — 값이 없습니다. Spring과 같은 값을 지정하세요.")
    elif key.lower() in DEV_KEYS:
        problems.append("INTERNAL_API_KEY — 저장소에 공개된 개발용 값입니다.")
    elif len(key) < MIN_KEY_LENGTH:
        problems.append(
            f"INTERNAL_API_KEY — {MIN_KEY_LENGTH}자 이상이어야 합니다. "
            f"`openssl rand -hex {MIN_KEY_LENGTH}` 로 만드세요."
        )

    _endpoint(env, "GITHUB_MCP_URL", REAL_GITHUB_MCP, problems)
    _endpoint(env, "NOTION_MCP_URL", REAL_NOTION_MCP, problems)
    return problems


def _endpoint(env: Mapping[str, str], name: str, expected_host: str, problems: list[str]) -> None:
    """Unset is fine: the defaults in collect/publish already point at the real thing."""
    url = (env.get(name) or "").strip()
    if url and expected_host not in url:
        problems.append(f"{name} — 실제 MCP({expected_host})가 아닙니다. 스텁을 가리키고 있습니다.")
