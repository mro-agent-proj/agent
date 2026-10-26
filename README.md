# MRO 정비 근거 MCP (business-mcp · rag-mcp)

AI 서버가 정비 요청을 판단할 때 쓰는 **읽기 전용 MCP 서버 두 개(도구 9개)**와, 그 근거 데이터(SDR 사례·AMM 참조·JASC)를 만드는 적재 도구입니다.
Python 3.12, PostgreSQL 16 + pgvector, MCP Streamable HTTP로 동작합니다. 업무 상태를 바꾸거나 LLM을 실행하지 않습니다. 전체 구성·통합 실행은 [integration](https://github.com/mro-agent-proj/integration)을 참고하세요.

```
[backend] ──run.requested + 토큰 2개──▶ [ai: LLM1·2·3] ──Authorization: Bearer──▶ [business-mcp :8101]  ┐
    │                                                                         [rag-mcp      :8102]  ┤ 이 레포
    └── Postgres (업무·RAG·기술자료, 스키마·데이터는 backend가 소유) ◀── SELECT 전용 계정 ───────────────┘
```

## 서버 2개

| 서버 | 주소 | DB 계정 | 읽는 데이터 | 토큰 aud |
| --- | --- | --- | --- | --- |
| business-mcp | `:8101/mcp` | `mro_business_reader` | Backend AI용 뷰 `agent_request_context`·`agent_run_context`(V9), `maintenance_history`, `departments` | `mro-business` |
| rag-mcp | `:8102/mcp` | `mro_reader` | RAG(Backend V3): `jasc_codes`, `sdr_cases`, `amm_*`, `evidence`, `knowledge_*` · 기술자료(Backend V5): `tech_*` · 실행 범위 `rag_request_contexts` | `mro-rag` |

두 서버 모두 읽기 전용이고, 업무 상태를 바꾸거나 LLM을 실행하지 않습니다. 쓰기 도구(B04~B06, R06)는 등록하지 않습니다.

## 도구 9개

| ID | 도구 | 하는 일 | 데이터 | 누가 부르나 |
| --- | --- | --- | --- | --- |
| B01 | `get_request_context` | 접수 내용·상태와 backend가 이 실행에 등록한 검색 범위 | 뷰 `agent_request_context`·`agent_run_context` | 실행기: 시작·최종 확정 직전 |
| B02 | `get_aircraft_history` | 같은 기체의 접수 이전 수리이력 | `maintenance_history` 1,600 | LLM1, LLM3, 최종 검증 (LLM2는 차단) |
| B03 | `search_departments` | 정비 부서와 담당 ATA | `departments` 8 | 실행기: 단계마다 고정 확인 |
| R01 | `search_jasc_codes` | JASC 코드 후보와 공식 명칭·정의 | `jasc_codes` 548 | LLM2·LLM3 |
| R02 | `search_sdr_cases` | 허용 모델 범위의 SDR 사례, 단어·벡터 하이브리드 | `sdr_cases`·`rag.sdr_embeddings` 71,741 | LLM2·LLM3 |
| R03 | `search_amm_references` | AMM 번호로 인용 SDR 문장 조회 (번호 조회만) | `amm_references` 35,396 | 쓰지 않음 (LLM 도구 목록에서 제외) |
| R04 | `search_technical_docs` | Airbus Safety First 정비 기사 의미 검색 | `tech_*` 61편·496조각 | LLM2·LLM3 |
| R05 | `get_evidence` | R02·R03·R04 근거의 원문과 인용 위치 재확인 | `evidence` 107,137 | LLM2·LLM3, 최종 근거 검증 |
| 확장 | `get_sdr_amm_references` | 근거 SDR에 인용된 AMM 번호·문장 | `evidence`, `amm_references` | 배정 확정 후 실행기 |

- 모든 도구는 `request_id`(토큰과 같아야 함)를, rag-mcp 도구는 `context_version`도 받습니다. 검색 도구는 `limit`·`cursor`로 페이지를 넘깁니다.
- **AMM은 판단 근거가 아니라 배정 후 참고 정보**입니다. 확장 도구가 근거 SDR의 AMM 번호를 정비팀 화면의 "참고 AMM"으로 붙입니다.
- B02 차단은 AI 서버의 LangGraph 입력 격리·도구 gateway에서 합니다. MCP 토큰은 서버(business/rag)만 구분하고 LLM 단계별 권한은 구분하지 않습니다.
- 입력 스키마·근거 ID 형식·과거 호출 수·검색 방식 상세는 [docs/TOOLS.md](docs/TOOLS.md)에 있습니다. 도구 계약 원본은 `docs/spec-v1/mcp-tool-contracts.json`이며 테스트가 일치를 확인합니다.

## 인증과 검색 범위

- 두 서버 모두 **backend가 실행마다 서명한 토큰**을 검증합니다(HS256, iss `mro-backend`). business는 aud `mro-business`, rag는 aud `mro-rag`이며, 다른 서버용 토큰은 401입니다. 헬스 경로(`/health`, `/health/live`, `/health/ready`)와 지표 `/metrics`만 토큰 없이 열려 있습니다.
- 도구는 **토큰에 적힌 `request_id`의 접수 건만** 읽습니다. 다른 요청을 조회하면 `FORBIDDEN`입니다.
- rag-mcp는 토큰의 `run_id`로 `rag_request_contexts`의 검색 범위(릴리스·색인·기종·고정 조건)를 찾아 고정합니다.
- **시연 정책**: backend가 운항사를 비우고 기종만 고정합니다(KAL·AAR·JNA는 SDR 운항사가 아님). 운항사가 없는 범위에서는 운항사 필터를 적용하지 않고 `operator_filter_applied=false`로 알립니다. 운항사가 지정되면 기존처럼 엄격하게 필터링합니다.

## 검색 방식 요약

- **R02 하이브리드**(`explore`): 단어 검색 상위 50 + 의미 검색 상위 50을 RRF(k=60)로 합칩니다. 질의는 OpenAI `text-embedding-3-large`(1024차원)로 임베딩하며 `RAG_OPENAI_API_KEY`가 필요합니다. 키·임베딩이 없거나 호출이 실패하면 단어 검색만 하고 `VECTOR_UNAVAILABLE` 경고를 남깁니다. `search_mode="exact"`는 구절 일치만 씁니다.
- **효과**(평가셋 `backend/data/eval/sdr`, 상위 5건 ATA 일치): SDR 원문 영어 52% → 68%, 증상 영어 59% → 74%, 증상 한국어 28% → 64%. 질의당 중앙값 0.42초.
- **R01·단어 검색**: 원문이 영어라 영어 핵심어 2~5개가 좋습니다. 한국어 단어는 단어 검색에서 무시됩니다.
- **R04**: 정비 기사 23편만, 접수 기종군 또는 전 기종 기사만 돌려줍니다. 적용 범위 미상 기사는 `include_unknown_applicability=true`일 때만 포함합니다.
- 검색 점수는 진단 정확도나 정답 확률이 아닙니다. AMM 자료는 SDR에서 추출한 참조번호·인용 맥락이지 실제 정비 절차가 아닙니다.

## 실행 (backend와 함께)

backend가 먼저 떠 있어야 합니다(Postgres, Flyway V1~V11, 시드, RAG·기술자료·SDR 임베딩 복원). 두 MCP는 backend의 Docker 네트워크(`backend_default`)에 붙습니다.

```bash
cp .env.mcp.example .env.mcp      # 값 채우기 (아래 표)
docker compose --env-file .env.mcp -f compose.mcp.yaml up -d --build
curl http://127.0.0.1:8101/health/ready && curl http://127.0.0.1:8102/health/ready
```

| `.env.mcp` | 값 |
| --- | --- |
| `BUSINESS_DATABASE_URL` | `postgresql+asyncpg://mro_business_reader:<비밀번호>@postgres:5432/mro` (로컬 기본 비밀번호 `local-business-reader-only`) |
| `RAG_DATABASE_URL` | `postgresql+asyncpg://mro_reader:<비밀번호>@postgres:5432/mro` (로컬 기본 `local-reader-only`) |
| `BUSINESS_JWT_SECRET` | backend `MCP_BUSINESS_JWT_SECRET`과 같은 값 (backend local 기본값은 `.env.mcp.example` 주석 참고) |
| `RAG_JWT_SECRET` | backend `MCP_RAG_JWT_SECRET`과 같은 값 |
| `RAG_CURSOR_SECRET` | 임의의 32자 이상, JWT 비밀값과 다르게 |
| `RAG_OPENAI_API_KEY` | R02 하이브리드 질의 임베딩용 OpenAI 키. 비우면 단어 검색만 함 |
| `BACKEND_NETWORK` | `backend_default` |

- rag-mcp 이미지는 **로컬 임베딩 모델과 PyTorch 없이** 동작합니다(약 500MB, 바로 기동). 의미 검색(R02·R04)은 질의만 OpenAI로 임베딩합니다. 예전 `rag-model-cache` 볼륨은 더 쓰지 않으니 `docker volume rm mcp_rag-model-cache`로 지워도 됩니다.
- 로컬 데이터 제작(`mro_rag`, `make embed`)은 이전 e5 파이프라인을 그대로 둡니다(`--extra embedding`). 전달본의 SDR·기술자료 임베딩은 `scripts/embed_openai.py`로 만듭니다.
- 호스트 포트는 `127.0.0.1`에만 엽니다. 같은 Docker 네트워크 안에서는 `http://business-mcp:8101/mcp`, `http://rag-mcp:8102/mcp`로 접근합니다.
- 이 Compose는 DB·RabbitMQ를 만들거나 DDL·데이터 적재를 하지 않습니다.
- 두 서버 모두 `GET /metrics`(Prometheus)를 토큰 없이 노출합니다: 도구별 호출 수·상태·오류 코드 `mro_mcp_tool_calls_total`, 지연 `mro_mcp_tool_call_duration_seconds`, 반환 건수 `mro_mcp_tool_returned_items`, 코드가 붙은 경고 `mro_mcp_tool_warnings_total{code}`(예: `VECTOR_UNAVAILABLE`, 코드 없는 안내 문구는 세지 않음).

## 데이터 제작 (로컬, 전달본 생성용)

공유 DB와 전체 데이터는 backend가 소유하고, 이 레포는 로컬 격리 DB에서 데이터를 만들고 검증한 뒤 덤프로 전달합니다.

- JASC 548개, SDR 71,741건, AMM 참조 35,396건, 근거 107,137개, 임베딩 31,678개(384차원, pgvector cosine 정확 검색)
- 원본 패키지(`data/source/airbus-mcp-data-v1`), 모델 가중치, 팀 PDF, 인증정보는 git에 없습니다.

```bash
uv sync --frozen --extra dev --extra embedding
cp .env.example .env              # 두 비밀값을 서로 다른 임의 값으로
make db migrate seed              # 로컬 DB 생성·마이그레이션·적재
make embed RELEASE=<release_key>  # 임베딩. 세부 순서는 docs/RUNBOOK.md
```

## 테스트

```bash
uv run --extra dev pytest                     # DB 없이 도는 테스트만 (나머지는 skip)
uv run --extra dev ruff check src tests scripts
```

테스트는 합성 데이터를 만들어 실행하므로 원본 파일 없이 재현됩니다. RAG DB·business DB 연결 테스트까지 돌리는 방법은 [docs/TESTING.md](docs/TESTING.md)(CI `.github/workflows/test.yml`과 같은 순서)를 참고하세요.

## 코드 구조

```
src/
├── mro_mcp/
│   ├── common/     MCP 서버 공통(응답 봉투, 오류, 읽기 전용 DB, 토큰 미들웨어)
│   ├── business/   business-mcp: B01~B03, 토큰의 request_id 확인
│   └── rag/        rag-mcp: R01~R05, 검색 범위 고정, cursor 서명 (tech.py: R04 테이블·기종군 판단)
└── mro_rag/        적재(ingest)·임베딩·컨텍스트(토큰 발급·검증, 범위 등록 참고 구현)·CLI
migrations/         로컬 제작 DB용 Alembic
docker/             business-mcp, rag-mcp Dockerfile
```

## 문서

| 문서 | 내용 |
| --- | --- |
| [TOOLS.md](docs/TOOLS.md) | 도구 상세 표, 검색 방식 전체 |
| [AI_SERVER_INTEGRATION.md](docs/AI_SERVER_INTEGRATION.md) | AI 서버 연결 상태, MCP 실행 설정 |
| [RUNBOOK.md](docs/RUNBOOK.md) | 격리 로컬 DB 제작, 적재, 임베딩 |
| [TESTING.md](docs/TESTING.md) | DB 연결 테스트 |
| [MODEL_SCOPE.md](docs/MODEL_SCOPE.md) | 모델 검색 범위 확장과 서비스 간 적용 순서 |
| [LIVE_EVALUATION_20261023.md](docs/LIVE_EVALUATION_20261023.md) | 2026-10-23 200건 실행의 단계별 도구 사용 |
| [DB_DESIGN.md](docs/DB_DESIGN.md), [DATA_MAPPING.md](docs/DATA_MAPPING.md), [INTEGRATION_CONTRACT.md](docs/INTEGRATION_CONTRACT.md) | DB 설계, 데이터 매핑, 도구 계약 |
| [integration/backend](integration/backend/README.md) | Backend 통합: Flyway SQL, 내부 컨텍스트 계약 |
| [TEAM_DISCUSSION.md](docs/TEAM_DISCUSSION.md), [WORK_PLAN.md](docs/WORK_PLAN.md) | 팀 논의(C01~C04), 작업 현황 |
| [implementation-review.md](reports/implementation-review.md), [AGENTS.md](AGENTS.md) | 검증 결과, 작업 규칙 |

## 알려진 한계

- 단어 검색은 영어 단어만 씁니다. 한국어 증상은 의미 검색 키가 있을 때만 직접 검색되고, 없으면 AI가 영어 핵심어로 바꿔야 합니다.
- 검색 범위는 시연 정책(기종만 고정)입니다. 항공사 × 기종 고정은 운항사 매핑을 정한 뒤 backend가 `operator_code`를 채우면 됩니다.
- 토큰 검증은 서명·만료·대상·요청 일치까지입니다. MCP 포트는 신뢰된 네트워크에만 둡니다.
