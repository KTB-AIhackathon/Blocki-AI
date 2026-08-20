"""Which GitHub repositories may enter a document candidate list.

Collect and analyze share this so a profile README cannot occupy a collect
slot and then "disappear" after scoring.
"""

from __future__ import annotations

import os
from typing import Any


def excluded_repos() -> set[str]:
    """`owner/name` the operator never wants in a document, comma separated."""
    raw = os.environ.get("BLOCKI_EXCLUDE_REPOS", "")
    return {part.strip().casefold() for part in raw.split(",") if part.strip()}


def is_blocked_repo(owner: str, name: str, *, fork: bool, archived: bool) -> bool:
    if f"{owner}/{name}".casefold() in excluded_repos():
        return True
    # `owner/owner` is the GitHub profile README, not a project.
    if name.casefold() == owner.casefold():
        return True
    return fork or archived


def listed_eligible(item: Any) -> bool:
    owner, name, fork, archived = _listed_fields(item)
    if owner is None or name is None:
        return False
    return not is_blocked_repo(owner, name, fork=fork, archived=archived)


def _listed_fields(item: Any) -> tuple[str | None, str | None, bool, bool]:
    owner = getattr(item, "owner", None)
    name = getattr(item, "name", None)
    if owner and name and not isinstance(item, dict):
        return str(owner), str(name), bool(getattr(item, "fork", False)), bool(getattr(item, "archived", False))
    if isinstance(item, str) and "/" in item:
        owner, name = item.split("/", 1)
        return owner, name, False, False
    if not isinstance(item, dict):
        return None, None, False, False
    owner = item.get("owner")
    if isinstance(owner, dict):
        owner = owner.get("login") or owner.get("name")
    name = item.get("name") or item.get("repo")
    if not owner or not name:
        full = item.get("full_name") or item.get("fullName")
        if isinstance(full, str) and "/" in full:
            owner, name = full.split("/", 1)
    return (
        str(owner) if owner else None,
        str(name) if name else None,
        bool(item.get("fork")),
        bool(item.get("archived")),
    )
