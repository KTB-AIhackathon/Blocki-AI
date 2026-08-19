from __future__ import annotations

import inspect
import json
import os
import re
from typing import Any, Callable

from app.contracts import (
    ErrorCode,
    ExecuteRequest,
    ExecuteResult,
    JobError,
    action_digest_of,
    is_allowed_readme_path,
)

def mcp_url() -> str:
    return os.environ.get("GITHUB_MCP_URL", "https://api.githubcopilot.com/mcp/")

CallTool = Callable[[str, dict[str, Any]], Any]

_SHA_RE = re.compile(r"SHA:\s*([0-9a-fA-F]{40})")
_STATUS_RE = re.compile(r"\b(401|403|404|409|422|429)\b")


class _GithubHttpError(Exception):
    def __init__(self, status: int | None, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class _Conflict(Exception):
    """GitHub 409/422 race on branch or PR create."""


async def execute_readme_pr(
    req: ExecuteRequest,
    github_pat: str,
    *,
    call_tool: CallTool | None = None,
) -> ExecuteResult:
    rejected = _validate(req)
    if rejected is not None:
        return rejected
    if call_tool is None:
        if not github_pat:
            return _rejected(req.execution_id, "github_auth", "GitHub authentication failed", False)
        return await _execute_with_mcp(req, github_pat)
    return await _execute_with_tools(req, call_tool)


def _validate(req: ExecuteRequest) -> ExecuteResult | None:
    action = req.action
    if getattr(action, "type", None) != "create_readme_pr":
        return _rejected(req.execution_id, "validation", "action.type must be create_readme_pr", False)
    if not is_allowed_readme_path(action.path):
        return _rejected(req.execution_id, "validation", "readme path not allowed", False)
    if req.idempotency_key != req.proposal_id:
        return _rejected(req.execution_id, "validation", "idempotency_key must equal proposal_id", False)
    if req.action_digest != action_digest_of(action):
        return _rejected(req.execution_id, "validation", "action_digest mismatch", False)
    head = _head_branch(req.proposal_id)
    if head == action.base_branch:
        return _rejected(req.execution_id, "validation", "refusing to write to the base branch", False)
    return None


def _head_branch(proposal_id: str) -> str:
    return f"blocki/readme-{proposal_id}"


async def _execute_with_mcp(req: ExecuteRequest, github_pat: str) -> ExecuteResult:
    from langchain_mcp_adapters.client import MultiServerMCPClient

    client = MultiServerMCPClient(
        {
            "github": {
                "transport": "http",
                "url": mcp_url(),
                "headers": {
                    "Authorization": f"Bearer {github_pat}",
                    "X-MCP-Toolsets": "repos,pull_requests",
                    "X-MCP-Readonly": "false",
                },
            }
        }
    )
    async with client.session("github") as session:

        async def mapped(name: str, args: dict[str, Any]) -> Any:
            return await _logical_via_mcp(session, name, args, base_branch=req.action.base_branch)

        return await _execute_with_tools(req, mapped)


async def _execute_with_tools(req: ExecuteRequest, call_tool: CallTool) -> ExecuteResult:
    action = req.action
    head = _head_branch(req.proposal_id)
    try:
        prs = await _list_prs(call_tool, action.owner, action.repo, head)
        if prs:
            return _duplicate(req.execution_id, prs[0]["url"])
        try:
            return await _sync_branch_and_pr(req, call_tool, head)
        except _Conflict:
            prs = await _list_prs(call_tool, action.owner, action.repo, head)
            if prs:
                return _duplicate(req.execution_id, prs[0]["url"])
            branch = await _get_branch(call_tool, action.owner, action.repo, head)
            if branch is None:
                return _rejected(req.execution_id, "internal", "GitHub conflict with no PR or branch", True)
            try:
                return await _from_existing_branch(req, call_tool, head, branch)
            except _Conflict:
                prs = await _list_prs(call_tool, action.owner, action.repo, head)
                if prs:
                    return _duplicate(req.execution_id, prs[0]["url"])
                return _rejected(req.execution_id, "internal", "GitHub conflict after retry", True)
    except _GithubHttpError as exc:
        return _http_result(req.execution_id, exc)
    except Exception:
        return _rejected(req.execution_id, "internal", "execution failed", False)


async def _sync_branch_and_pr(req: ExecuteRequest, call_tool: CallTool, head: str) -> ExecuteResult:
    action = req.action
    branch = await _get_branch(call_tool, action.owner, action.repo, head)
    if branch is not None:
        return await _from_existing_branch(req, call_tool, head, branch)
    return await _from_new_branch(req, call_tool, head)


async def _from_existing_branch(
    req: ExecuteRequest,
    call_tool: CallTool,
    head: str,
    branch: dict[str, Any],
) -> ExecuteResult:
    action = req.action
    blob = await _get_file(call_tool, action.owner, action.repo, action.path, head)
    if blob is not None and blob.get("content") == action.replacement_markdown:
        return await _create_pr(req, call_tool, head)
    blob_ok = (
        blob.get("blob_sha") == action.expected_blob_sha
        if blob is not None
        else not action.expected_blob_sha
    )
    if not blob_ok or branch.get("sha") != action.expected_base_sha:
        return _rejected(req.execution_id, "stale_sha", "expected SHA mismatch", False)
    await _update_file(req, call_tool, head)
    return await _create_pr(req, call_tool, head)


async def _from_new_branch(req: ExecuteRequest, call_tool: CallTool, head: str) -> ExecuteResult:
    action = req.action
    base = await _get_ref(call_tool, action.owner, action.repo, action.base_branch)
    blob = await _get_file(call_tool, action.owner, action.repo, action.path, action.base_branch)
    blob_ok = (
        blob.get("blob_sha") == action.expected_blob_sha
        if blob is not None
        else not action.expected_blob_sha
    )
    if base.get("sha") != action.expected_base_sha or not blob_ok:
        return _rejected(req.execution_id, "stale_sha", "expected SHA mismatch", False)
    await _invoke_write(
        call_tool,
        "create_branch",
        {
            "owner": action.owner,
            "repo": action.repo,
            "branch": head,
            "from_sha": action.expected_base_sha,
        },
    )
    await _update_file(req, call_tool, head)
    return await _create_pr(req, call_tool, head)


async def _update_file(req: ExecuteRequest, call_tool: CallTool, head: str) -> None:
    action = req.action
    if head == action.base_branch:
        raise _GithubHttpError(None, "refusing to write to the base branch")
    await _invoke_write(
        call_tool,
        "update_file",
        {
            "owner": action.owner,
            "repo": action.repo,
            "path": action.path,
            "content": action.replacement_markdown,
            "branch": head,
            "expected_blob_sha": action.expected_blob_sha,
        },
    )


async def _create_pr(req: ExecuteRequest, call_tool: CallTool, head: str) -> ExecuteResult:
    action = req.action
    result = await _invoke_write(
        call_tool,
        "create_pr",
        {
            "owner": action.owner,
            "repo": action.repo,
            "title": action.pr_title,
            "body": action.pr_body,
            "head": head,
            "base": action.base_branch,
        },
    )
    url = _url_of(result)
    if not url:
        return _rejected(req.execution_id, "internal", "create_pr returned no url", True)
    return ExecuteResult(execution_id=req.execution_id, status="created", pr_url=url, error=None)


async def _list_prs(call_tool: CallTool, owner: str, repo: str, head_branch: str) -> list[dict[str, Any]]:
    raw = await _invoke(call_tool, "list_prs", {"owner": owner, "repo": repo, "head_branch": head_branch})
    items = raw if isinstance(raw, list) else []
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        url = _url_of(item)
        if not url:
            continue
        out.append({"url": url, "state": item.get("state") or "", "number": item.get("number")})
    return out


async def _get_branch(call_tool: CallTool, owner: str, repo: str, branch: str) -> dict[str, Any] | None:
    raw = await _invoke(call_tool, "get_branch", {"owner": owner, "repo": repo, "branch": branch})
    if raw is None:
        return None
    sha = _sha_of(raw)
    return {"sha": sha} if sha else None


async def _get_file(
    call_tool: CallTool, owner: str, repo: str, path: str, ref: str
) -> dict[str, Any] | None:
    raw = await _invoke(call_tool, "get_file", {"owner": owner, "repo": repo, "path": path, "ref": ref})
    if raw is None:
        return None
    if isinstance(raw, dict):
        return {"blob_sha": raw.get("blob_sha") or raw.get("sha") or "", "content": raw.get("content") or ""}
    return None


async def _get_ref(call_tool: CallTool, owner: str, repo: str, ref: str) -> dict[str, Any]:
    raw = await _invoke(call_tool, "get_ref", {"owner": owner, "repo": repo, "ref": ref})
    sha = _sha_of(raw) if raw is not None else None
    if not sha:
        raise _GithubHttpError(404, "base ref not found")
    return {"sha": sha}


async def _invoke(call_tool: CallTool, name: str, args: dict[str, Any]) -> Any:
    last: Exception | None = None
    for attempt in range(3):
        try:
            result = call_tool(name, args)
            if inspect.isawaitable(result):
                result = await result
            return result
        except _GithubHttpError as exc:
            if exc.status == 429 and attempt < 2:
                last = exc
                continue
            raise
        except Exception as exc:
            status = _http_status(str(exc))
            if status == 429 and attempt < 2:
                last = exc
                continue
            if status is not None:
                raise _GithubHttpError(status, str(exc)) from exc
            raise
    assert last is not None
    raise last


async def _invoke_write(call_tool: CallTool, name: str, args: dict[str, Any]) -> Any:
    try:
        return await _invoke(call_tool, name, args)
    except _GithubHttpError as exc:
        if exc.status in (409, 422):
            raise _Conflict() from exc
        raise


async def _logical_via_mcp(
    session: Any, name: str, args: dict[str, Any], *, base_branch: str
) -> Any:
    owner = args["owner"]
    repo = args["repo"]
    if name == "list_prs":
        raw = await _mcp_call(
            session,
            "list_pull_requests",
            {
                "owner": owner,
                "repo": repo,
                "head": f"{owner}:{args['head_branch']}",
                "state": "all",
            },
        )
        return [_norm_pr(item) for item in _as_list(raw)]
    if name == "get_branch":
        try:
            raw = await _mcp_call(
                session,
                "get_commit",
                {"owner": owner, "repo": repo, "sha": args["branch"], "detail": "none"},
            )
        except _GithubHttpError as exc:
            if exc.status == 404:
                return None
            raise
        sha = _sha_of(raw)
        return {"sha": sha} if sha else None
    if name == "get_file":
        try:
            raw = await _mcp_call(
                session,
                "get_file_contents",
                {"owner": owner, "repo": repo, "path": args["path"], "ref": args["ref"]},
            )
        except _GithubHttpError as exc:
            if exc.status == 404:
                return None
            raise
        return _file_from_mcp(raw)
    if name == "get_ref":
        raw = await _mcp_call(
            session,
            "get_commit",
            {"owner": owner, "repo": repo, "sha": args["ref"], "detail": "none"},
        )
        sha = _sha_of(raw)
        return {"sha": sha} if sha else {"sha": ""}
    if name == "create_branch":
        await _mcp_call(
            session,
            "create_branch",
            {"owner": owner, "repo": repo, "branch": args["branch"], "from_branch": base_branch},
        )
        return None
    if name == "update_file":
        payload: dict[str, Any] = {
            "owner": owner,
            "repo": repo,
            "path": args["path"],
            "content": args["content"],
            "branch": args["branch"],
            "message": f"docs: update {args['path']}",
        }
        sha = args.get("expected_blob_sha")
        if sha:
            payload["sha"] = sha
        await _mcp_call(session, "create_or_update_file", payload)
        return None
    if name == "create_pr":
        raw = await _mcp_call(
            session,
            "create_pull_request",
            {
                "owner": owner,
                "repo": repo,
                "title": args["title"],
                "body": args["body"],
                "head": args["head"],
                "base": args["base"],
            },
        )
        return {"url": _url_of(raw)}
    raise ValueError(f"unknown tool {name}")


async def _mcp_call(session: Any, tool: str, arguments: dict[str, Any]) -> Any:
    last: Exception | None = None
    for attempt in range(3):
        try:
            result = await session.call_tool(tool, arguments)
            if _result_is_error(result):
                message = "\n".join(_iter_texts(result)) or "GitHub MCP error"
                status = _http_status(message)
                err = _GithubHttpError(status, message)
                if status == 429 and attempt < 2:
                    last = err
                    continue
                raise err
            payload = _json_payload(result)
            return payload if payload is not None else result
        except _GithubHttpError as exc:
            if exc.status == 429 and attempt < 2:
                last = exc
                continue
            raise
    assert last is not None
    raise last


def _file_from_mcp(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    texts = _iter_texts(raw) if not isinstance(raw, dict) else []
    joined = "\n".join(texts)
    sha = None
    match = _SHA_RE.search(joined)
    if match:
        sha = match.group(1)
    content = None
    for text in texts:
        if text.startswith("successfully downloaded"):
            continue
        content = text
    parsed = raw if isinstance(raw, dict) else _json_payload(raw)
    if isinstance(parsed, dict):
        sha = sha or parsed.get("blob_sha") or parsed.get("sha")
        if parsed.get("content") is not None:
            content = parsed.get("content")
    if sha is None and content is None:
        return None
    return {"blob_sha": sha or "", "content": content or ""}


def _norm_pr(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {"url": "", "state": "", "number": None}
    return {
        "url": _url_of(item) or "",
        "state": item.get("state") or "",
        "number": item.get("number"),
    }


def _as_list(payload: Any) -> list[Any]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("pull_requests", "items", "data", "result"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        if payload.get("number") or payload.get("url") or payload.get("html_url"):
            return [payload]
    return []


def _json_payload(result: Any) -> Any:
    if isinstance(result, (dict, list)):
        return result
    structured = getattr(result, "structuredContent", None)
    if structured:
        return structured
    for text in _iter_texts(result):
        stripped = text.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                continue
    return None


def _iter_texts(result: Any) -> list[str]:
    if isinstance(result, str):
        return [result]
    texts: list[str] = []
    content = getattr(result, "content", None)
    if not content:
        return texts
    for block in content:
        if isinstance(block, dict):
            if block.get("type") == "text" and block.get("text"):
                texts.append(block["text"])
            resource = block.get("resource")
            if isinstance(resource, dict) and resource.get("text"):
                texts.append(resource["text"])
            continue
        text = getattr(block, "text", None)
        if text:
            texts.append(text)
        resource = getattr(block, "resource", None)
        if resource is not None:
            resource_text = getattr(resource, "text", None)
            if resource_text:
                texts.append(resource_text)
    return texts


def _result_is_error(result: Any) -> bool:
    return bool(getattr(result, "isError", False))


def _sha_of(raw: Any) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw or None
    if not isinstance(raw, dict):
        parsed = _json_payload(raw)
        return _sha_of(parsed) if parsed is not raw else None
    sha = raw.get("sha")
    if sha:
        return str(sha)
    obj = raw.get("object")
    if isinstance(obj, dict) and obj.get("sha"):
        return str(obj["sha"])
    return None


def _url_of(raw: Any) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw or None
    if isinstance(raw, dict):
        url = raw.get("url") or raw.get("html_url") or raw.get("URL")
        return str(url) if url else None
    return None


def _http_status(message: str) -> int | None:
    lower = message.lower()
    if "rate limit" in lower:
        return 429
    if any(token in lower for token in ("bad credentials", "unauthorized", "requires authentication")):
        return 401
    if "sha mismatch" in lower or "doesn't match" in lower or "does not match" in lower:
        return 409
    if "already exists" in lower:
        return 422
    if "not found" in lower:
        return 404
    match = _STATUS_RE.search(message)
    return int(match.group(1)) if match else None


def _http_result(execution_id: str, err: _GithubHttpError) -> ExecuteResult:
    if err.status in (401, 403):
        return _rejected(execution_id, "github_auth", "GitHub authentication failed", False)
    if err.status == 429:
        return _rejected(execution_id, "github_rate_limit", "GitHub rate limit", True)
    return _rejected(execution_id, "internal", "GitHub request failed", True)


def _duplicate(execution_id: str, url: str) -> ExecuteResult:
    return ExecuteResult(execution_id=execution_id, status="duplicate", pr_url=url, error=None)


def _rejected(execution_id: str, code: ErrorCode, message: str, retryable: bool) -> ExecuteResult:
    return ExecuteResult(
        execution_id=execution_id,
        status="rejected",
        pr_url=None,
        error=JobError(code=code, message=message, retryable=retryable),
    )
