"""Process logs for the worker.

Spring writes failures to stdout and `docker logs` keeps them. This module is
the same idea: one line the operator can grep, never a token.
"""

from __future__ import annotations

import logging
import os

_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def configure() -> None:
    level_name = (os.environ.get("LOG_LEVEL") or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    if not root.handlers:
        logging.basicConfig(level=level, format=_FORMAT)


def redact(text: str, *secrets: str) -> str:
    out = text or ""
    for secret in secrets:
        if secret:
            out = out.replace(secret, "«redacted»")
    return out[:400]
