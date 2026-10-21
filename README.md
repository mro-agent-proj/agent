# MRO 정비 근거 MCP (business-mcp · rag-mcp)

AI 서버가 정비 요청을 판단할 때 쓰는 **읽기 전용 MCP 서버 두 개**와, 그 근거 데이터(SDR 사례·AMM 참조·JASC)를 만드는 적재 도구입니다.
Python 3.12, PostgreSQL 16 + pgvector, MCP Streamable HTTP로 동작합니다. 업무 상태를 바꾸거나 LLM을 실행하지 않습니다.

## 전체 구성에서의 위치

```
[backend] ──run.requested + 토큰 2개──▶ [ai: LLM1·2·3] ──Authorization: Bearer──▶ [business-mcp :8101]  ┐
    │                                                                         [rag-mcp      :8102]  ┤ 이 레포
    └── Postgres (업무·RAG·기술자료, 스키마·데이터는 backend가 소유) ◀── SELECT 전용 계정 ───────────────┘
```

| 레포 | 역할 |
| --- | --- |
| [dashboard](https://github.com/mro-agent-proj/dashboard) | 신고자·정비팀·관리자 화면 |
| [backend](https://github.com/mro-agent-proj/backend) | DB·계정·데이터 복원, 실행별 검색 범위 등록과 MCP 토큰 발급 |
| [LLM](https://github.com/mro-agent-proj/LLM) | AI 서버. 이 레포의 두 MCP를 `streamable_http`로 호출 |
| [agent](https://github.com/mro-agent-proj/agent) | 이 레포 |

## 도구

| 서버 | ID | 도구 | 설명 |
| --- | --- | --- | --- |
| business-mcp (8101) | B01 | `get_request_context` | 접수 내용, 기체, 현재 상태, 검색 범위(`model_key`) |
| | B02 | `get_aircraft_history` | 같은 기체의 이전 수리이력(MH-*), 접수 시각 이전만 |
| | B03 | `search_departments` | 정비 부서와 담당 ATA. JASC 후보로 순위만 조정 |
| rag-mcp (8102) | R01 | `search_jasc_codes` | JASC 코드 후보·정의 (영어 단어 검색) |
| | R02 | `search_sdr_cases` | 같은 기종의 SDR 사례 (영어 전문검색) |
| | R03 | `search_amm_references` | SDR에 인용된 AMM 번호와 인용 맥락 (한국어·영어 의미 검색) |
| | R05 | `get_evidence` | R02·R03에서 받은 근거의 원문과 인용 위치 재확인 |

도구 제목과 입력 스키마는 `docs/spec-v1/mcp-tool-contracts.json`과 일치해야 합니다(테스트가 확인). 도구 설명(description)은 AI가 도구를 고를 때 읽는 문구이므로, 검색 요령을 여기에 적습니다.

### 인증과 검색 범위

- 두 서버 모두 **backend가 실행마다 서명한 토큰**을 검증합니다(HS256, iss `mro-backend`). business는 aud `mro-business`, rag는 aud `mro-rag`이며, 다른 서버용 토큰은 401입니다. 헬스 경로(`/health`, `/health/live`, `/health/ready`)만 토큰 없이 열려 있습니다.
- 도구는 **토큰에 적힌 `request_id`의 접수 건만** 읽습니다. 다른 요청을 조회하면 `FORBIDDEN`입니다.
- rag-mcp는 토큰의 `run_id`로 `rag_request_contexts`의 검색 범위(릴리스·색인·기종·고정 조건)를 찾아 고정합니다.
- **시연 정책**: backend가 운항사를 비우고 기종만 고정합니다(KAL·AAR·JNA는 SDR 운항사가 아님). 운항사가 없는 범위에서는 운항사 필터를 적용하지 않고 `operator_filter_applied=false`로 알립니다. 운항사가 지정되면 기존처럼 엄격하게 필터링합니다.

### 검색 방식

- SDR·JASC 원문은 영어입니다. 검색어는 영어 핵심어 2~5개가 좋습니다(예: `brake vibration taxi`, `FLAP SYS 1 FAULT`). 한국어 단어는 무시됩니다.
- R02 기본 모드(`explore`)와 R01은 **단어가 하나라도 맞으면** 많이 맞는 순으로 돌려줍니다. R02 `search_mode="exact"`는 구절 그대로 찾습니다.
- R03 질의는 `intfloat/multilingual-e5-small` 임베딩으로 의미 검색합니다(한국어 가능, CPU).
- AMM 자료는 SDR에서 추출한 **참조번호와 인용 맥락**입니다. 실제 정비 매뉴얼 절차나 검증된 적용성이 아닙니다. 검색 점수는 진단 정확도나 정답 확률이 아닙니다.

## 실행 (backend와 함께)

backend가 먼저 떠 있어야 합니다(Postgres, Flyway V1~V6, 시드, RAG 복원). 두 MCP는 backend의 Docker 네트워크(`backend_default`)에 붙습니다.

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
| `BACKEND_NETWORK` | `backend_default` |

- rag-mcp 이미지는 **CPU 전용 PyTorch**를 씁니다(약 2GB, 깨끗한 빌드 1분 안팎). 처음 시작할 때 고정 리비전의 임베딩 모델을 내려받아 `rag-model-cache` 볼륨에 보관하고, 모델을 미리 올린 뒤에 요청을 받습니다.
- 호스트 포트는 `127.0.0.1`에만 엽니다. 같은 Docker 네트워크 안에서는 `http://business-mcp:8101/mcp`, `http://rag-mcp:8102/mcp`로 접근합니다.
- 이 Compose는 DB·RabbitMQ를 만들거나 DDL·데이터 적재를 하지 않습니다.

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
uv run --extra dev pytest                     # DB 없이 도는 테스트만 실행 (나머지는 skip)
uv run --extra dev ruff check src tests scripts
```

DB 테스트까지 돌리려면 CI(`.github/workflows/test.yml`)처럼 빈 pgvector DB를 준비합니다.

```bash
export TEST_DATABASE_URL=postgresql+asyncpg://mro_owner:local-dev-only@127.0.0.1:55432/mro_test
export TEST_RAG_DATABASE_URL=postgresql+asyncpg://mro_reader:local-reader-only@127.0.0.1:55432/mro_test
DATABASE_URL="$TEST_DATABASE_URL" uv run alembic upgrade head
psql ... -f scripts/create_reader.sql
uv run --extra dev pytest                     # 33개 + backend 실DB가 필요한 1개 skip
```

테스트는 독립 합성 데이터를 만들어 실행하므로 원본 파일이 없어도 재현됩니다.

## 코드 구조

```
src/
├── mro_mcp/
│   ├── common/     MCP 서버 공통(응답 봉투, 오류, 읽기 전용 DB, 토큰 미들웨어)
│   ├── business/   business-mcp: B01~B03, 토큰의 request_id 확인
│   └── rag/        rag-mcp: R01~R03·R05, 검색 범위 고정, cursor 서명
└── mro_rag/        적재(ingest)·임베딩·컨텍스트(토큰 발급·검증, 범위 등록 참고 구현)·CLI
migrations/         로컬 제작 DB용 Alembic
docker/             business-mcp, rag-mcp Dockerfile
```

## 문서

1. [AI 서버 연결 안내](docs/AI_SERVER_INTEGRATION.md): 연결 상태, MCP 실행 설정
2. [실행 안내](docs/RUNBOOK.md): 격리 로컬 DB 제작, 적재, 임베딩, 테스트
3. [Backend 통합 안내](integration/backend/README.md): Flyway SQL, 내부 컨텍스트 계약
4. [DB 설계](docs/DB_DESIGN.md), [데이터 매핑](docs/DATA_MAPPING.md), [도구 계약](docs/INTEGRATION_CONTRACT.md)
5. [팀 논의 사항](docs/TEAM_DISCUSSION.md) (C01~C04 반영 현황), [작업 현황](docs/WORK_PLAN.md)
6. [검증 결과](reports/implementation-review.md), [작업 규칙](AGENTS.md)

## 알려진 한계

- SDR 검색은 영어 단어만 씁니다. 한국어 증상은 AI가 영어 핵심어로 바꿔 검색해야 합니다.
- 검색 범위는 시연 정책(기종만 고정)입니다. 항공사 × 기종 고정은 운항사 매핑을 정한 뒤 backend가 `operator_code`를 채우면 됩니다.
- 토큰 검증은 서명·만료·대상·요청 일치까지입니다. 네트워크 제한은 요청 권한 검증을 대체하지 않으므로 MCP 포트는 신뢰된 네트워크에만 둡니다.
