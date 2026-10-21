# FastAPI AI 서버와 MCP 연결

기준: [LLM PR #1](https://github.com/mro-agent-proj/LLM/pull/1)의
`4dd40f3`(FastAPI + RabbitMQ 실행기), agent의 FastMCP PR
[#3](https://github.com/mro-agent-proj/agent/pull/3)·[#4](https://github.com/mro-agent-proj/agent/pull/4).
이 문서는 연결 구성을 설명하며, 아직 결정되지 않은 인증 전달 방식을 팀 합의로 확정하지 않는다.

## 프로세스와 책임

| 프로세스 | 저장소 | 주소/역할 |
|---|---|---|
| FastAPI AI 서버 | LLM | 8100, 상태 확인·RabbitMQ 실행기·LangChain/LangGraph LLM1/2/3 |
| business-mcp | agent | 8101 `/mcp`, B01/B02/B03, `mro_business_reader` |
| rag-mcp | agent | 8102 `/mcp`, R01/R02/R03/R05, `mro_reader` |
| Spring·PostgreSQL·RabbitMQ | backend | DB·계정·데이터·큐 선언·업무 상태 변경 |

AI는 `MultiServerMCPClient`의 `streamable_http`로 두 서버를 호출한다.
MCP를 AI FastAPI 앱에 마운트하거나 별도 REST 검색 API로 바꾸지 않는다.
공통 코드는 `src/mro_mcp/common`, 업무 도구는 `business`, 검색 도구는 `rag`에 있다.
두 서버는 각각 `mro-business-mcp`와 `mro-rag-mcp`로 실행한다.
ASGI 배포가 필요하면 `mro_mcp.business.server:create_app` 또는
`mro_mcp.rag.server:create_app`을 Uvicorn `--factory`로 실행할 수 있다.

## MCP 실행

1. Backend 담당자가 DB·조회 계정·Flyway·시드·RAG 덤프 복원을 준비한다.
2. `cp .env.mcp.example .env.mcp` 후 실제 조회 계정 URL과 비밀값을 채운다.
   URL 비밀번호의 특수문자는 URL 인코딩한다. `.env.mcp`는 Git에 넣지 않는다.
3. `BACKEND_NETWORK`를 Backend Compose의 실제 네트워크 이름에 맞춘다.
4. `docker compose --env-file .env.mcp -f compose.mcp.yaml up -d --build`를 실행한다.
5. 두 컨테이너가 healthy인지 확인한 후 AI 서버를 시작한다.

이 Compose는 DB·RabbitMQ를 만들거나 DDL/데이터 적재를 실행하지 않는다.
호스트 포트는 loopback에만 공개한다. 같은 Docker 네트워크 안에서는 서비스 이름으로 접근한다.
business-mcp에는 요청별 인증이 아직 없어 해당 네트워크는 신뢰된 서버만 연결해야 한다.
네트워크 제한은 요청 권한 검증을 대체하지 않는다.

RAG 이미지는 `embedding` 의존성을 포함하고, 첫 시작 시 고정된
`intfloat/multilingual-e5-small` 리비전을 캐시에 내려받아 미리 로드한다.
첫 호출에서 모델 다운로드·로딩이 발생해 AI의 MCP 10초 제한을 넘기는 일을 줄이기 위한 설정이다.
모델 로딩 실패 시 서버 시작도 실패한다. 처음에는 외부 모델 다운로드 접근이 필요하며,
가중치는 `rag-model-cache` 볼륨에 보관한다. DB 데이터나 새 임베딩을 생성하지 않는다.
실제 검색 시간이 10초 이내라는 보장은 별도 성능 검증이 필요하다.

## AI 환경 설정

AI를 호스트에서 실행할 때 LLM 저장소의 `.env`에 설정한다.

```dotenv
BUSINESS_MCP_URL=http://127.0.0.1:8101/mcp
RAG_MCP_URL=http://127.0.0.1:8102/mcp
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o-mini
TOOL_TIMEOUT_SECONDS=10
TOOL_RETRIES=1
```

AI도 Backend Docker 네트워크에 연결했다면 각각
`http://business-mcp:8101/mcp`, `http://rag-mcp:8102/mcp`를 사용한다.
컨테이너 안의 `localhost`는 다른 MCP 컨테이너를 가리키지 않는다.
OpenAI API 키는 AI 서버에만 설정하며 MCP 서버에는 필요 없다.

Spring↔AI 실행 요청·이벤트 전달은 RabbitMQ를 유지한다.
실제 소비는 위임 컨텍스트 연결 후 `AI_MODE=rabbit`, `RABBITMQ_URL`을 설정해 켠다.
현재 AI PR의 `MCP_DELEGATION_TRANSPORT=amqp_headers` 및
`x-mro-business-token`/`x-mro-rag-token`은 선택 가능한 **제안**이며 Backend 구현 합의가 필요하다.
토큰은 요청별 HTTP Authorization 헤더로 전달한다. 전역 공용 토큰이나 요청 ID로 대체하지 않는다.
MCP 프로세스는 RabbitMQ를 소비하거나 LLM을 실행하지 않는다.

## 상태 확인의 범위

- `/health/live`: 프로세스가 HTTP 응답 가능한지 확인한다.
- `/health/ready`: DB ping 성공 시 200, 실패 시 503이다. RAG preload 사용 시 모델 준비 이후에 서버가 열린다.
- 기존 `/health`는 `/health/ready`와 같은 동작을 유지한다.
- 세 주소는 토큰 없이 호출할 수 있다. `/mcp`의 RAG 인증은 유지한다.
- healthy는 도구별 SELECT 권한, 데이터 완전성, 요청 권한·검색 범위의 일치까지 보장하지 않는다.

## 연결 상태 (2026-10-21 갱신)

아래 조건 1·2·3은 Backend `McpDelegationService`·Flyway V6와 이 저장소의 business 인증으로 연결했다.

- 업무 조회 권한: Backend V6가 `repair_requests`의 아래 8개 열만 `mro_business_reader`에 연다.
- business 요청 인증: business-mcp도 Backend가 서명한 토큰(aud `mro-business`, `BUSINESS_JWT_SECRET`)을 검증하고,
  도구는 토큰의 `request_id`와 같은 접수 건만 읽는다. 다르면 `FORBIDDEN`.
- 동일 실행 컨텍스트: Backend가 실행마다 `run_id`를 만들어 `rag_request_contexts`(context_version 1)를 등록하고,
  같은 `run_id`·`request_id`·`sub`(신고자)·`tenant_id`(항공사)로 두 토큰을 서명해 AMQP 헤더
  `x-mro-business-token`·`x-mro-rag-token`으로 보낸다. B01도 context_version 1을 돌려준다.
- 시연 검색 범위(C02): Backend 컨텍스트는 `operator_code`를 비워 두고 기종(`model_key`)만 고정한다.
  rag-mcp는 운항사가 없는 컨텍스트에서 운항사 필터를 적용하지 않는다(`operator_filter_applied=false`).
  KAL/AAR/JNA를 SDR 운항사에 대응시키지 않는다. 항공사×모델 고정 규칙으로 바꾸려면 운항사 매핑을 정한 뒤
  Backend가 `operator_code`를 채우면 된다.

## 전체 통합 전에 남았던 조건 (기록)

1. **업무 조회 권한:** Backend의 후속 Flyway에서 `mro_business_reader`가 B01/B02/B03의
   요청 확인에 필요한 `repair_requests` 열을 읽을 수 있어야 한다.
   필요한 열은 `id`, `tail_number`, `aircraft_model`, `airline_code`, `symptom_text`,
   `status`, `created_at`, `version`이다. 공유 DB에 직접 GRANT하지 않는다.
2. **business 요청 인증:** 현재 business 서버는 AI가 보낸 Bearer를 검증하지 않는다.
   요청별 위임 검증과 권한 확인을 팀 계약으로 정해 구현해야 한다.
3. **동일 실행 컨텍스트:** 현재 B01은 `context_version=1`, operator 미설정 범위를 반환한다.
   RAG는 서명 토큰과 `rag_request_contexts`의 요청·run·subject·tenant·버전·release/index를 확인한다.
   B01과 RAG에 같은 검증된 범위를 전달하도록 Backend·MCP 계약을 맞춰야 한다.
   KAL/AAR/JNA를 원본 SDR 운항사에 임의 대응시키지 않는다.
4. **AI 실행 조건:** Backend가 요청별 토큰·유효 컨텍스트를 제공해야 현재 AI 실행기가 동작한다.
   MCP 주소와 건강 상태만 맞춘 단계는 전체 서비스 연결 완료가 아니다.

이번 연결 구성 변경에서 공유 DB 변경·복원·애플리케이션 종단 간 검증은 수행하지 않았다.
