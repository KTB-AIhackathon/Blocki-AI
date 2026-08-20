from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.contracts.common import JobError, sha256_hex
from app.contracts.readme import ReadmePrAction

ExecuteStatus = Literal["created", "duplicate", "rejected"]


def action_digest_of(action: ReadmePrAction) -> str:
    return sha256_hex(action.model_dump(mode="json"))


class ExecuteRequest(BaseModel):
    execution_id: str
    proposal_id: str
    action_digest: str
    action: ReadmePrAction
    idempotency_key: str


class ExecuteResult(BaseModel):
    execution_id: str
    status: ExecuteStatus
    pr_url: str | None = None
    error: JobError | None = None
