# Blocki-AI

Spring만 호출하는 내부 FastAPI 워커다. 브라우저는 이 서버를 치지 않는다.

하는 일: 유저 GitHub PAT로 remote GitHub MCP를 읽고, 진행 메모·이력서/포폴·README 초안을 만든 뒤, 승인된 README만 PR로 연다.

하지 않는 일: 유저 DB, 토큰 금고, 공개 OAuth, Job 상태 저장.

Notion TIL 수집기는 `app/collect/notion.py`에 있다. 자체 MCP OAuth로 붙고 토큰은 로컬 파일에 둔다. Spring이 토큰을 대신 들고 오는 구조가 확정되면 이 부분을 걷어낸다.

설계: [`DESIGN.md`](DESIGN.md). Spring이 맞춰야 할 공개 명세 수정은 `Docs/spring-api-revision.md`.

## 실행

필요 환경변수:

| 이름 | 필수 | 설명 |
| --- | --- | --- |
| `INTERNAL_API_KEY` | 예 | Spring과 공유하는 내부 키. 없으면 `/internal/*` 는 503 |
| `GITHUB_MCP_URL` | 아니오 | 기본 `https://api.githubcopilot.com/mcp/` |
| `JOB_TIMEOUT` | 아니오 | 초. 기본 `60` |
| `PORT` | 아니오 | 기본 `8000` |

```bash
cd Blocki-AI
uv sync --extra dev
export INTERNAL_API_KEY=dev-internal-key
uv run python -m app
# 또는
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Docker:

```bash
docker build -t blocki-ai .
docker run --rm -p 8000:8000 -e INTERNAL_API_KEY=dev-internal-key blocki-ai
```

확인:

```bash
curl -s localhost:8000/health
# {"ok":true}
```

테스트:

```bash
uv sync --extra dev
uv run pytest -q
```

실 GitHub MCP 핑:

```bash
GITHUB_PAT=ghp_... uv run python scripts/ping_github_mcp.py
```

## Spring이 호출하는 API

`POST /internal/jobs`

- Header: `X-Internal-Key`, `X-GitHub-Pat`
- Body: `JobRequest` (`job_type` = `progress_summary` | `profile_document` | `readme_proposal`)
- 200: `JobResult`. PAT는 body/응답에 넣지 않는다.
- 키 없음/틀림 → 401

`POST /internal/executions`

- Header: 동일
- Body: `ExecuteRequest` (승인된 README `action` + `action_digest`)
- 200: `ExecuteResult` (`created` | `duplicate` | `rejected`)

`GET /health` — 인증 없음.

공개 `/api/v1` 은 이 서버에 없다. 그건 Spring이다.
