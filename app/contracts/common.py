from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel

INTERNAL_KEY_HEADER = "X-Internal-Key"
GITHUB_PAT_HEADER = "X-GitHub-Pat"
NOTION_TOKEN_HEADER = "X-Notion-Token"

ErrorCode = Literal[
    "missing_pat",
    "github_auth",
    # Valid token, too narrow a scope. Distinct from github_auth because the user has to
    # re-consent to more permissions rather than reconnect the same ones.
    "github_scope",
    "github_rate_limit",
    "mcp_unavailable",
    "llm_failed",
    "blocked",
    "stale_sha",
    "duplicate",
    "internal",
    "validation",
]


class JobError(BaseModel):
    code: ErrorCode
    message: str
    retryable: bool = False


class GitHubCollectError(Exception):
    def __init__(self, error: JobError) -> None:
        super().__init__(error.message)
        self.error = error


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def iso_utc(value: datetime) -> str:
    dt = as_utc(value)
    assert dt is not None
    return dt.isoformat().replace("+00:00", "Z")


def canonical_dumps(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return iso_utc(value)
    raise TypeError(f"unserializable: {type(value)!r}")


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(canonical_dumps(value).encode("utf-8")).hexdigest()
