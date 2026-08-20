"""Process logs for the worker.

Spring writes failures to stdout and `docker logs` keeps them. This module is
the same idea: one line the operator can grep, never a token.
"""

from __future__ import annotations

import logging
import os
import traceback

_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_UNTITLED = frozenset({"", "새 페이지", "untitled", "new page", "untitled page"})


def configure() -> None:
    level_name = (os.environ.get("LOG_LEVEL") or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    if not root.handlers:
        logging.basicConfig(level=level, format=_FORMAT)


def redact(text: str, *secrets: str, limit: int = 400) -> str:
    out = text or ""
    for secret in secrets:
        if secret:
            out = out.replace(secret, "«redacted»")
    return out[:limit]


def redact_exc(exc: BaseException, *secrets: str) -> str:
    head = f"{type(exc).__name__}: {exc}"
    stack = "".join(traceback.format_exception(exc))
    return redact(f"{head}\n{stack}", *secrets, limit=2000)


def short_id(value: str | None) -> str:
    text = (value or "").strip()
    if not text:
        return "-"
    return text[-8:]


def title_untitled(title: str | None) -> bool:
    return (title or "").strip().lower() in _UNTITLED
