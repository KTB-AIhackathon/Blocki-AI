# Blocki-AI

Spring만 호출하는 내부 FastAPI 워커다. 브라우저는 이 서버를 치지 않는다.

하는 일: 유저 GitHub PAT로 GitHub MCP를 읽고 → 사실만 추출하고 → 진행 메모·포트폴리오·
이력서·README 초안을 만들고 → 완성된 `.md`를 Notion 날짜 로그로 올리면서 Spring에는
그대로 돌려준다. 승인된 README만 PR로 연다.

하지 않는 일: 유저 DB, 토큰 금고, 공개 OAuth, Job 상태 저장, 스케줄링.

설계는 [`DESIGN.md`](DESIGN.md). Spring 쪽 계약은 `Docs/spring-api-revision.md`.

## 구조

```
app/
  contracts/   공유 타입
  collect/     GitHub MCP 읽기 (결정적)
  analyze/     스냅샷 → Evidence (사실만)
  pipelines/   progress · portfolio · resume · readme
  llm/         provider 격리 + 근거 가드
  publish/     Notion 업로드 (정책 / 전송 / 스키마 적응)
  execute/     README PR (유일한 쓰기 경로)
  api/         /internal/jobs · /internal/executions
```

의존은 한 방향이다 (`contracts ← collect/analyze/llm ← pipelines ← api`).
`tests/test_boundaries.py`가 import를 읽어서 강제한다.

## 실행

| 환경변수 | 필수 | 설명 |
| --- | --- | --- |
| `INTERNAL_API_KEY` | 예 | Spring과 공유하는 내부 키. 없으면 `/internal/*` 는 503 |
| `GITHUB_MCP_URL` | 아니오 | 기본 `https://api.githubcopilot.com/mcp/` |
| `NOTION_MCP_URL` | 아니오 | 기본 `https://mcp.notion.com/mcp` |
| `BLOCKI_LLM_PROVIDER` | 아니오 | `auto`(기본) · `anthropic` · `none` |
| `BLOCKI_LLM_MODEL` | 아니오 | 기본 `claude-sonnet-4-5` |
| `ANTHROPIC_API_KEY` | 배포에서 예 | Claude. 있으면 `auto`가 Anthropic을 고른다 |
| `JOB_TIMEOUT` | 아니오 | 초. 기본 `90` |
| `PORT` | 아니오 | 기본 `8000` |

```bash
uv sync --extra dev
export INTERNAL_API_KEY=dev-internal-key
uv run python -m app
```

Docker:

```bash
docker build -t blocki-ai .
docker run --rm -p 8000:8000 -e INTERNAL_API_KEY=dev-internal-key blocki-ai
```

확인: `curl -s localhost:8000/health` → `{"ok":true}`

테스트: `uv run pytest -q`

워커·Spring·프론트를 한 번에 띄우는 건 워크스페이스 루트의 `docker-compose.yml`이다.

```bash
./up.sh
python3 e2e/run_stack.py
```

## 이력서의 빈칸

경력과 학력은 GitHub에서 나오지 않는다. 없다고 job을 막지도, 지어내지도 않는다.
해당 절을 "직접 채워주세요"로 남긴 채 문서를 완성하고, 같은 문서가 Notion으로 올라가므로
사용자는 그 페이지에서 채우면 된다. 빈칸으로 남은 항목은 `unresolved_fields`에 실려
화면이 무엇이 비었는지 말할 수 있다. 문서 제목이 되는 `name`만 여전히 필수다.

## LLM

provider를 아는 파일은 `app/llm/client.py` 하나다.

```bash
uv sync --extra anthropic
export ANTHROPIC_API_KEY=sk-ant-...
```

provider가 없어도 파이프라인은 돈다. LLM은 문장을 다듬을 뿐이고, 근거 id를 대지 못한
문장은 `app/llm/guard.py`가 버린 뒤 결정적 문장으로 되돌린다.

## Notion

쓰는 곳은 사용자 개인 루트 바로 아래의 **Developer TIL Dashboard** 한 장과 그 자손뿐이다.
구조는 `Docs/노션템플릿.md` 3·4·5절을 그대로 옮긴 `app/publish/notion_template.py`가 갖고 있다.

Spring이 `X-Notion-Token`으로 access token을 넘기면, 문서가 완성된 뒤 같은 `.md`를
대시보드 자식인 날짜 로그 페이지(`포트폴리오 YYYY-MM-DD` 등)로 올리고 `page_id`/`page_url`을
`JobResult.notion`에 담아 돌려준다. 같은 제목이 이미 있으면 본문만 갈아끼운다.
토큰이 없으면 문서 생성만 하고 이 단계를 건너뛴다. Notion이 죽어도 job은 성공한다.

`notion.parent_id`는 매번 대시보드인지 제목으로 확인하고 쓴다. 저장된 id는 주장일 뿐이고
그 id가 가리키는 페이지가 사실이라, OAuth `workspace_id`를 잘못 넘기면 쓰지 않고 건너뛴다.
가드는 `app/publish/notion_dashboard.py`, 확인은 `tests/test_notion_dashboard.py`에 있다.

스키마는 Notion이 소유하므로 코드에 박지 않고 서버가 광고한 것을 읽어서 채운다.
실서버 스키마가 바뀌면 `app/publish/notion_schema.py`와 그 테스트 픽스처만 고친다.

실제 토큰으로 확인:

```bash
NOTION_TOKEN=ntn_... NOTION_PARENT_ID=<page id> uv run python scripts/verify_notion.py
# 도구 목록 → 실서버 스키마 → 우리가 보낼 인자까지 출력 (쓰기 없음)

# 실제로 한 장 써보고 되읽어서 마크다운 손실까지 확인
NOTION_TOKEN=ntn_... NOTION_PARENT_ID=<page id> uv run python scripts/verify_notion.py --write
```

## Spring이 호출하는 API

`POST /internal/jobs`

- Header: `X-Internal-Key`, `X-GitHub-Pat`, `X-Notion-Token`(선택)
- Body: `JobRequest`. `job_type` = `progress_summary` | `portfolio` | `resume` | `readme_proposal`
  (구버전 `profile_document`도 받는다. `document.kind`로 자동 변환)
- 200: `JobResult`. 토큰은 body·응답·로그 어디에도 넣지 않는다.
- 내부 키 없음/틀림 → 401, 필수 필드 누락 → 422

`JobResult`는 최상위에 `status` · `error_code` · `missing_sources`를 함께 싣는다.
Spring의 `DocumentGenerationClient.validate()`가 그 자리만 보고, 안 맞으면 응답 전체를
버리기 때문이다. 세 값은 저장되는 필드가 아니라 `proposal.status` · `error.code` ·
스냅샷에서 파생되므로 서로 어긋날 수 없다.

| 최상위 | 값 | 나오는 곳 |
| --- | --- | --- |
| `status` | `proposed` \| `partial` \| `no_change` \| `failed` | `proposal.status`. `blocked`는 Spring이 아는 실패어가 하나뿐이라 `failed`로 접힌다 |
| `error_code` | `ok: false`일 때 항상 채워진다 | `error.code` 또는 `proposal.error.code` |
| `missing_sources` | `[]` 또는 `["GITHUB"]` | 스냅샷에 저장소가 하나도 없을 때만 `GITHUB`. 경력란이 비어서 `partial`인 건 여기 해당하지 않는다 |

이 규칙은 `tests/test_spring_contract.py`가 Java 쪽 `validate()`를 그대로 옮겨 검사한다.

`POST /internal/notion/dashboard`

- Header: `X-Internal-Key`, `X-Notion-Token`
- Body: `{"user_id": "...", "known_page_id": "..."}` — `known_page_id`는 Spring이 저장해 둔 것.
  주면 확인 한 번으로 끝나고, 없으면 개인 루트를 검색한 뒤 없을 때만 3절 트리를 만든다.
- 200: `{"ok", "page_id", "page_url", "created", "error"}`. Spring은 `page_id`를 저장했다가
  이후 job의 `notion.parent_id`로 쓴다. `workspace_id`와 섞으면 안 된다.
- Notion이 죽어도 200에 `ok: false`로 답한다. 연결 자체를 실패시키지 않기 위해서다.

`POST /internal/executions`

- Header: `X-Internal-Key`, `X-GitHub-Pat`
- Body: `ExecuteRequest` (승인된 README `action` + `action_digest`)
- 200: `ExecuteResult` (`created` | `duplicate` | `rejected`)

`GET /health` — 인증 없음.

공개 `/api/v1` 은 이 서버에 없다. 그건 Spring이다.
