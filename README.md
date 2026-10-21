# Airbus 정비 근거 DB·MCP

SDR 사례·AMM 참조·JASC를 PostgreSQL 16과 pgvector에 적재하고, 읽기 전용 MCP로 조회합니다. Python 3.12 기반이며 LLM2·LLM3와 Backend 업무 처리는 포함하지 않습니다.

## 구현 상태 · 2026-10-21

- `http://127.0.0.1:8101/mcp`: Streamable HTTP, B01 `get_request_context`, B02 `get_aircraft_history`, B03 `search_departments`.
- `http://127.0.0.1:8102/mcp`: Streamable HTTP, R01 `search_jasc_codes`, R02 `search_sdr_cases`, R03 `search_amm_references`, R05 `get_evidence`.
- JASC 548개, SDR 71,741건, AMM 참조 35,396건, 근거 107,137개 적재 검증.
- 로컬 다국어 `intfloat/multilingual-e5-small` 384차원 임베딩 31,678개. 모델 리비전·전처리 고정, pgvector cosine 정확 검색.
- 두 MCP 모두 Backend가 실행마다 서명한 위임 토큰(business aud `mro-business`, RAG aud `mro-rag`)을 검증. RAG는 범위·버전·색인 고정, 서명 cursor. 두 MCP 모두 SELECT 전용 DB 역할. 시연 범위는 기종만 고정(운항사 없음).
- 기존 데이터/RAG 검증 이력: 로컬 PostgreSQL·HTTP MCP·한국어/영어 검색→근거 원문 일치. 이번 FastAPI 연결 구성의 실행 검증 결과는 아님.

**Backend와의 종단 간 연결은 아직입니다.** 최신 팀 인터페이스 본문 v0.3과 Backend `0fc8caf` 기준으로 pgvector·Flyway V3/V4·덤프 복원 스크립트는 Backend에 반영됐습니다. 공유 DB·전체 데이터 관리는 Backend가 소유하고 이 저장소는 로컬 제작·검증 후 덤프를 전달합니다. 인증/컨텍스트·시연 검색 범위 결정과 AI의 완료 전 근거 검증 연결이 남아 있습니다. `business-mcp`와 `rag-mcp`는 이 저장소에 구현되어 있습니다. FastAPI AI 서버·RabbitMQ 소비자·LLM1/2/3은 별도 LLM 저장소에서 실행합니다. [AI 서버 연결 안내](docs/AI_SERVER_INTEGRATION.md)에 두 MCP의 실행 설정과 남은 연결 조건을 정리했습니다.

## 실행·문서

1. [실행 안내](docs/RUNBOOK.md): 격리 로컬 DB 제작, 적재, 임베딩, MCP 실행, 테스트
2. [Backend 통합 안내](integration/backend/README.md): 현재 Backend 기준, Flyway SQL, 내부 컨텍스트 계약
3. [DB 설계](docs/DB_DESIGN.md), [데이터 매핑](docs/DATA_MAPPING.md), [도구 계약](docs/INTEGRATION_CONTRACT.md)
4. [검증 결과](reports/implementation-review.md), [전체 데이터 실행 결과](reports/runtime-verification.json)
5. [팀 확인 사항](docs/TEAM_DISCUSSION.md), [작업 현황](docs/WORK_PLAN.md)
6. [작업 규칙](AGENTS.md), [팀 v0.3 변경·충돌 검토](reports/team-interface-v03-review.md)

```sh
uv sync --frozen --extra dev --extra embedding
cp .env.example .env
# .env의 두 비밀값을 서로 다른 임의 값으로 교체한 후 RUNBOOK 순서대로 실행
```

원본 패키지·모델 가중치·팀 PDF·Google 문서 보존본·인증정보는 Git에 포함하지 않습니다. 전체 데이터 실행에는 별도 `data/source/airbus-mcp-data-v1` 패키지가 필요합니다. 자동 테스트는 독립 합성 데이터를 만들어 실행하므로 원본 파일이 없어도 재현됩니다.

AMM 자료는 SDR에서 추출한 **참조번호와 인용 맥락**입니다. 실제 정비 매뉴얼 절차나 검증된 적용성으로 취급하지 않습니다. 검색 점수는 진단 정확도나 정답 확률이 아닙니다.
