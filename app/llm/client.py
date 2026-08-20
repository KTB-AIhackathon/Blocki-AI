"""The only module that knows which LLM vendor we are on.

Everything downstream sees a LangChain `BaseChatModel` and uses
`ainvoke` / `with_structured_output`. Local and deploy both use Anthropic.

Environment:
  BLOCKI_LLM_PROVIDER   auto (default) | anthropic | none
  BLOCKI_LLM_MODEL      provider-specific model id
  ANTHROPIC_API_KEY     required by the anthropic provider
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Any

logger = logging.getLogger(__name__)

ANTHROPIC_DEFAULT_MODEL = "claude-sonnet-4-5"


def provider() -> str:
    configured = (os.environ.get("BLOCKI_LLM_PROVIDER") or "auto").strip().lower()
    if configured != "auto":
        return configured
    if os.environ.get("ANTHROPIC_API_KEY") and _importable("langchain_anthropic"):
        return "anthropic"
    return "none"


@lru_cache(maxsize=1)
def get_llm() -> Any | None:
    """Shared chat model, or None when no provider is configured.

    Callers must treat None as "render deterministically" rather than as an
    error: a document without polish is still a correct document.
    """
    name = provider()
    if name == "none":
        return None
    try:
        return _build(name)
    except Exception as exc:
        logger.warning("llm provider %s unavailable: %s", name, exc)
        return None


def reset() -> None:
    get_llm.cache_clear()


def _build(name: str) -> Any:
    if name == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=os.environ.get("BLOCKI_LLM_MODEL", ANTHROPIC_DEFAULT_MODEL),
            temperature=0,
            timeout=float(os.environ.get("LLM_TIMEOUT", "60")),
            max_retries=1,
        )

    raise ValueError(f"unknown llm provider: {name}")


def _importable(module: str) -> bool:
    from importlib.util import find_spec

    try:
        return find_spec(module) is not None
    except (ImportError, ValueError):
        return False
