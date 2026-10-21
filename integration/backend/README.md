# Backend 연결 안내

확인 기준: [backend 9d38555](https://github.com/mro-agent-proj/backend/tree/9d38555e42db45f94b0aabfe16d5401c5cd0ffb6), 2026-10-21. 해당 저장소를 수정하거나 업로드하지 않았습니다. 이 디렉터리는 Backend 담당자가 검토·통합할 자료입니다.

## 실제 구조와 DB 통합

현재 Backend는 Spring Boot·Flyway·PostgreSQL 16·RabbitMQ입니다. PDF의 Python/FastAPI/Alembic 환경보다 실제 저장소를 연결 기준으로 사용합니다. Python 3.12는 이 RAG 서비스의 런타임입니다.

1. Backend PostgreSQL 이미지를 pgvector가 설치된 PG16 이미지로 변경합니다. 로컬 검증 이미지는 `pgvector/pgvector:0.8.2-pg16`입니다. 기존 `postgres:16-alpine`에는 확장이 포함되지 않습니다. 기존 데이터 볼륨의 이관은 별도 검토 후 수행합니다.
2. [V3__rag_evidence.sql](V3__rag_evidence.sql)을 Backend의 `src/main/resources/db/migration/`에 통합합니다. 기준 커밋은 V2까지 있으므로 V3로 제공하며, 합칠 때 새 마이그레이션이 생겼으면 다음 번호로 조정합니다.
3. Backend Flyway가 먼저 구조를 생성한 후 이 저장소의 `mro-rag ingest`와 `embed`를 운영 계정으로 실행합니다. 적재 코드는 DDL을 실행하지 않습니다. 공유 DB에 Python Alembic을 추가로 실행하지 않습니다.
4. RAG 조회 계정에는 열 개 RAG 테이블에만 SELECT를 부여합니다. 로컬 예시는 `scripts/create_reader.sql`이며 실제 계정 비밀번호는 별도 주입합니다. Backend의 users 등 업무 테이블 SELECT는 필요하지 않습니다.

별도 검증 DB에서 Backend V1→V2→제공 V3 SQL 적용에 성공했습니다. 기존 maintenance_history의 `jasc_code`와 새 RAG 테이블은 충돌하지 않습니다. Flyway 애플리케이션 기동·RabbitMQ 연결 전체를 검증한 것은 아닙니다. 로컬 Alembic과 제공 SQL의 동일성은 자동 테스트합니다.

## 현재 연결을 막는 세 항목

### 1. 신뢰된 실행 컨텍스트와 위임 인증

현재 `RunPayload.java`는 requestId/tailNumber/aircraftModel/airlineCode/symptomText만 전달합니다. 검색 조건 버전·실행 스냅샷·위임 주체가 없습니다. 로그인 JWT도 이 서비스의 위임 JWT와 규격이 다릅니다.

Backend는 접수 권한을 확인한 뒤 `public.rag_request_contexts`에 다음 값을 기록해야 합니다. Python의 `mro_rag.context.publish_context`는 실행 가능한 참조 구현이며 Java Backend에 자동 연결되지는 않습니다.

| 값 | 규칙 |
|---|---|
| run_id | 실행 식별자, 새 실행마다 고유 |
| request_id | 실제 repair_requests.id 문자열 |
| context_version | 접수별 단조 증가하는 검색 조건 버전. JPA 낙관적 잠금 version과 자동 동일시하지 않음 |
| subject_id, tenant_id | Backend가 접근권한을 확인한 사용자·범위 식별자. LLM 입력 금지 |
| operator_code, model_key | 검증된 서비스→SDR 매핑. 미확정은 null이며 범위 검색 NEEDS_INPUT |
| release_id | READY knowledge_releases.id |
| index_ids | 고정된 READY index_key 배열. 자료 종류+검색 방식마다 최대 하나 |
| mapping_revision | 사용한 검증 매핑 버전 |
| locked_filters | jasc_codes 배열, difficulty_from/to 날짜 또는 null. 후보 JASC와 구분 |
| active, expires_at | 철회·만료 제어 |

새 컨텍스트는 접수 단위 트랜잭션/잠금으로 버전 역행을 막고 이전 버전을 비활성화합니다. 같은 run 내용을 수정하지 않습니다. 개인정보·권한이 변경되면 active=false로 철회하고 이후 호출이 거부되는지 확인합니다.

토큰은 HS256, `iss=mro-backend`, `aud=mro-rag`, `sub`, `tenant_id`, `run_id`, `request_id`, 양의 정수 `context_version`, `iat`, `exp`를 포함합니다. 기본 유효기간 10분이며 JWT 키와 cursor 키는 별도 관리합니다. Backend만 발급하고 MCP 클라이언트가 HTTP 헤더로 전달합니다. 실제 로그인 토큰을 그대로 재사용하지 않습니다. 서버는 서명 검증 후 매번 DB 컨텍스트·권한·버전을 다시 확인합니다.

RabbitMQ의 `mro.agent.run` 소비자/이벤트 발행기는 이 작업 범위에 없습니다. Backend가 큐를 선언하는 현재 책임을 유지하고, 후속 Agent 연결 단계에서 run_id와 위임 토큰을 신뢰된 실행 메타데이터로 전달합니다. LLM 공개 inputSchema는 변경하지 않습니다.

### 2. 배정 전 근거 검증

현재 `AgentEventService.complete()`의 ASSIGN 경로는 부서 존재·활성 여부를 확인하고 배정을 진행합니다. SDR/AMM 근거의 존재·범위·버전을 검증하는 코드는 해당 경로에서 확인되지 않았습니다.

Agent가 전달하는 SDR/AMM `Evidence.ref_id`에는 R02/R03 결과의 **evidence_id**를 넣고, Backend가 같은 접수·버전·run으로 R05를 호출해 검증한 뒤 배정해야 합니다. `source_id`, AMM 번호, `MH-*` 이력 ID를 혼용하지 않습니다. HISTORY 근거는 업무 DB/B02에서 별도로 검증합니다. 같은 evidence_group_id의 SDR과 파생 AMM은 독립 근거 두 개로 세지 않습니다. 배정 시점에 철회·버전 변경도 재확인해야 합니다.

이 저장소는 조회 서비스만 제공하므로 Backend 배정 안전장치가 구현되었다고 표시하지 않습니다.

### 3. KAL/AAR/JNA와 SDR 코드 매핑

Backend 항공사 3개와 검색 데이터 operator_code 24개는 코드 체계가 다릅니다. 검증된 매핑 근거가 제공되지 않았으므로 임의 대응이나 전체 항공사 검색으로 우회하지 않습니다. 정확 모델도 A320 계열로 확대하지 않습니다. 미확정 컨텍스트는 NEEDS_INPUT으로 반환합니다. 전체 데이터 실행 검증은 원본 AALA/A321231 범위를 사용했고 KAL 연동을 증명하지 않습니다.

## 팀 시드 반영 사항

- aircraft 20대: KAL 7 / AAR 7 / JNA 6. 상세 모델 10종.
- maintenance_history 1,499건: 모든 jasc_code가 4자리이며 현재 548개 코드표에 존재합니다. V2 컬럼은 nullable이므로 미래 null을 추정으로 채우지 않습니다.
- ATA 2자리와 JASC 4자리를 별도로 보존합니다. S21의 3230/3242, S22의 2160/2150을 앞 두 자리로 합치지 않습니다.
- scenarios.md는 S01~S22 + V1~V6, 총 28건입니다. 이 저장소의 14개 테스트와 별개이며 28개 업무 시나리오 전체를 통과했다고 주장하지 않습니다.
- 실제 CSV reported_at 범위는 **2024-01-16T11:38:00Z~2026-10-19T10:52:00Z**로, 메시지의 2025-10-21~2026-10-20과 다릅니다. 시나리오 날짜 기준은 팀 시드 파일과 맞춰야 합니다.
- 업무 시드는 Backend SeedLoader가 소유합니다. RAG 적재기는 aircraft/maintenance_history를 복제하거나 덮어쓰지 않습니다.
