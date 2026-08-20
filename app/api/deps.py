from __future__ import annotations

import os
import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException

from app.contracts import GITHUB_PAT_HEADER, INTERNAL_KEY_HEADER, NOTION_TOKEN_HEADER

DEV_KEY = "dev-internal-key"

GitHubPat = Annotated[str | None, Header(alias=GITHUB_PAT_HEADER)]
NotionToken = Annotated[str | None, Header(alias=NOTION_TOKEN_HEADER)]


def require_internal_key(
    x_internal_key: Annotated[str | None, Header(alias=INTERNAL_KEY_HEADER)] = None,
) -> None:
    expected = os.environ.get("INTERNAL_API_KEY")
    if not expected and os.environ.get("PYTEST_CURRENT_TEST"):
        expected = DEV_KEY
    if not expected:
        raise HTTPException(status_code=503, detail="INTERNAL_API_KEY not set")
    if x_internal_key is None or not secrets.compare_digest(x_internal_key, expected):
        raise HTTPException(status_code=401, detail="unauthorized")


InternalKey = Depends(require_internal_key)
