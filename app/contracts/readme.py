from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, field_validator

README_PATH_RE = re.compile(r"^(docs/)?README(\.(md|markdown|rst|txt))?$", re.IGNORECASE)


def is_allowed_readme_path(path: str) -> bool:
    if not path or ".." in path or path.startswith("/") or "\\" in path:
        return False
    return README_PATH_RE.fullmatch(path) is not None


class ReadmeTarget(BaseModel):
    owner: str
    repo: str
    path: str = "README.md"

    @field_validator("path")
    @classmethod
    def _path_ok(cls, value: str) -> str:
        if not is_allowed_readme_path(value):
            raise ValueError("readme path not allowed")
        return value


class ReadmePrAction(BaseModel):
    type: Literal["create_readme_pr"] = "create_readme_pr"
    owner: str
    repo: str
    path: str
    base_branch: str
    expected_base_sha: str
    expected_blob_sha: str
    replacement_markdown: str
    pr_title: str
    pr_body: str

    @field_validator("path")
    @classmethod
    def _path_ok(cls, value: str) -> str:
        if not is_allowed_readme_path(value):
            raise ValueError("readme path not allowed")
        return value
