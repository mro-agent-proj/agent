# Backend 연결 안내

> 2026-10-23 변경: 아래 초기 정확 모델 검색 설명은 [단계별 모델 범위 계약](../../docs/MODEL_SCOPE.md)으로 보완된다. 최신 실행 범위·배포 의존성은 해당 문서를 우선한다.

확인 기준: [backend 0fc8caf](https://github.com/mro-agent-proj/backend/tree/0fc8caf575b5a1a89e8fbdaa9989866f5654d9ac), 2026-10-21. 최신 팀 PDF 본문 v0.3을 반영했습니다. 이번 작업에서 Backend 저장소는 읽기만 했습니다. 세부 충돌은 [규약 검토 보고서](../../reports/team-interface-v03-review.md)에 있습니다.

## 실제 구조와 DB 통합

현재 Backend와 최신 PDF 모두 Spring Boot·Flyway·PostgreSQL 16+pgvector·RabbitMQ입니다. Python 3.12는 AI/RAG 서비스 런타임입니다. 구 PDF의 FastAPI/Alembic 공통 환경은 더 이상 팀 기준이 아닙니다.

1. **반영됨:** Backend Compose는 `pgvector/pgvector:0.8.2-pg16`을 사용합니다. 공유 연결 포트는 5432이며 이 저장소의 55432 DB는 격리된 제작·검증용입니다.
2. **반영됨:** Backend V3는 [제공 SQL](V3__rag_evidence.sql)에 출처 주석 2줄만 추가한 동일 DDL입니다. 적용된 V3를 수정하지 않고 이후 구조 변경은 다음 Flyway SQL을 Backend PR로 제출합니다.
3. **반영됨:** Backend V4가 RAG 열 개 테이블에 대한 `mro_reader` SELECT와 기본 읽기 전용 트랜잭션을 설정합니다. business 계정은 별도입니다. 비밀번호/LOGIN은 Backend 초기화 절차가 소유합니다.
4. **반영됨:** `scripts/rag-restore.sh`가 데이터 9개 테이블의 data-only dump를 복원합니다. 실제 공유 DB 복원 완료 여부는 이번 저장소 검토만으로 확인하지 않았습니다. 우리 ingest/embed를 공유 DB에 실행하지 않습니다.

Backend 담당 실행 순서는 다음과 같습니다. 명령은 Backend 저장소에서 실행합니다.

```sh
docker compose up -d
AI_MODE=rabbit ./gradlew bootRun
# Flyway V1~V4/업무 시드 완료 후 다른 터미널에서
./scripts/rag-restore.sh data/rag/2026-10-21-rag-v1
```

전달 ZIP을 풀어 해당 경로에 dump·V3·manifest·SHA256SUMS·verify.sql 등을 함께 둡니다. 현재 복원은 별도 명령이며 Spring 기동 시 자동 실행되지 않습니다. 모든 RAG 데이터 테이블이 비어 있을 때만 복원하고 동일 release는 내용 검증 후 건너뜁니다. 일부 적재/다른 release는 덮어쓰지 않습니다. `rag_request_contexts`는 전달 덤프에서 제외된 런타임 테이블입니다.

RAG 서버는 `RAG_DATABASE_URL=postgresql+asyncpg://mro_reader:local-reader-only@localhost:5432/mro`로 연결합니다(로컬 기본 비밀번호). 인증·컨텍스트 C01이 남아 있으므로 URL 변경만으로 실제 접수 도구 호출이 완성되지는 않습니다. 기존 볼륨에 계정이 없다면 Backend 담당자가 계정/권한을 보완하며 데이터를 지우는 초기화를 자동 실행하지 않습니다.

기존 로컬 검증에서는 Backend V1/V2/제공 V3 공존과 전달 덤프 복원을 확인했습니다. 이번에는 최신 Backend V3의 내용 일치를 재확인했습니다. Spring·RabbitMQ·AI의 전체 연동을 실행한 것은 아닙니다.

## 현재 연결을 막는 항목

### 1. 신뢰된 실행 컨텍스트와 위임 인증 · C01

현재 `RunPayload.java`는 requestId/tailNumber/aircraftModel/airlineCode/symptomText만 전달합니다. 검색 조건 버전·실행 스냅샷·위임 주체가 없습니다. 로그인 JWT도 이 서비스의 위임 JWT와 규격이 다릅니다.

최신 PDF 29쪽은 내부 전달 방식을 미정으로 둡니다. 아래는 **현재 RAG 구현을 연결할 경우의 계약**이며 팀 최종 결정은 아닙니다. 이 방식을 선택하면 Backend가 접수 권한 확인 후 `public.rag_request_contexts`에 다음 값을 기록합니다. Python의 `mro_rag.context.publish_context`는 실행 가능한 참조 구현이며 Java Backend에 자동 연결되지 않습니다. 다른 방식을 택해도 Google 명세의 요청 권한·조건 버전·고정 스냅샷 요구를 유지해야 합니다.

| 값 | 규칙 |
|---|---|
| run_id | 실행 식별자, 새 실행마다 고유 |
| request_id | 실제 repair_requests.id 문자열 |
| context_version | 검색 조건 변경 시 증가하는 접수별 버전. 같은 조건의 새 run은 같은 버전 사용 가능. JPA 낙관적 잠금 version과 자동 동일시하지 않음 |
| subject_id, tenant_id | Backend가 접근권한을 확인한 사용자·범위 식별자. LLM 입력 금지 |
| operator_code, model_key | 검증된 서비스→SDR 매핑. 미확정은 null이며 범위 검색 NEEDS_INPUT |
| release_id | READY knowledge_releases.id |
| index_ids | 고정된 READY index_key 배열. 자료 종류+검색 방식마다 최대 하나 |
| mapping_revision | 사용한 검증 매핑 버전 |
| locked_filters | jasc_codes 배열, difficulty_from/to 날짜 또는 null. 후보 JASC와 구분 |
| active, expires_at | 철회·만료 제어 |

새 컨텍스트는 접수 단위 트랜잭션/잠금으로 버전 역행을 막고 이전 버전을 비활성화합니다. 같은 run 내용을 수정하지 않습니다. 개인정보·권한이 변경되면 active=false로 철회하고 이후 호출이 거부되는지 확인합니다.

토큰은 HS256, `iss=mro-backend`, `aud=mro-rag`, `sub`, `tenant_id`, `run_id`, `request_id`, 양의 정수 `context_version`, `iat`, `exp`를 포함합니다. 기본 유효기간 10분이며 JWT 키와 cursor 키는 별도 관리합니다. Backend만 발급하고 MCP 클라이언트가 HTTP 헤더로 전달합니다. 실제 로그인 토큰을 그대로 재사용하지 않습니다. 서버는 서명 검증 후 매번 DB 컨텍스트·권한·버전을 다시 확인합니다.

RabbitMQ의 `mro.agent.run` 소비자/이벤트 발행기는 이번 문서 갱신 범위에 없습니다. 이 구현안을 채택할 경우 후속 Agent 연결에서 run_id와 위임 토큰을 신뢰된 실행 메타데이터로 전달합니다. LLM 공개 inputSchema에는 넣지 않습니다. 도구 정의 캐시와 호출별 Authorization 헤더를 분리하고, 한 run의 토큰을 전체 요청에 재사용하지 않습니다. 현재 서버는 무인증 initialize/list_tools도 거부하므로 시작 시 무인증 도구 로드 예시와의 조정이 필요합니다.

### 2. AI의 배정 전 근거 검증과 실패 사유 · C03

현재 `AgentEventService.complete()`의 ASSIGN 경로는 부서 존재·활성 여부를 확인하고 배정을 진행합니다. SDR/AMM 근거의 존재·범위·버전을 검증하는 코드는 해당 경로에서 확인되지 않았습니다.

최신 PDF 21쪽에 따라 **AI 실행기**가 COMPLETED 전에 같은 접수·버전·run으로 B02/R05를 호출해 검증합니다. SDR/AMM `Evidence.ref_id`에는 R02/R03의 **evidence_id**, HISTORY에는 B02의 `MH-*`를 넣습니다. `source_id`나 AMM 번호를 혼용하지 않고 동일 evidence_group_id를 독립 근거 두 개로 세지 않습니다. 검증 통과 후 AI가 ASSIGN 이벤트를 보내고 Spring이 배정을 실행합니다. Spring이 R05를 직접 호출해야 한다는 이전 문구는 최신 담당 경계에 맞춰 수정했습니다.

AI가 근거 검증 실패로 HUMAN_REVIEW/VALIDATION_FAILED를 보내도 현재 Backend는 사유를 AGENT_NEEDS_HUMAN으로 바꿉니다. Backend PR로 허용 사유 처리와 저장 결과 테스트를 수정해야 합니다. AI 검증을 생략한 ASSIGN은 현재 Spring의 부서 검사만으로 근거 오류를 잡을 수 없으므로 전체 연결 완료로 표시하지 않습니다.

### 3. KAL/AAR/JNA와 SDR 코드 매핑 · C02

Backend 항공사 3개와 검색 데이터 operator_code 24개는 코드 체계가 다릅니다. 검증된 매핑 근거가 제공되지 않았으므로 임의 대응이나 전체 항공사 검색으로 우회하지 않습니다. 정확 모델도 A320 계열로 확대하지 않습니다. 미확정 컨텍스트는 NEEDS_INPUT으로 반환합니다. 전체 데이터 실행 검증은 원본 AALA/A321231 범위를 사용했고 KAL 연동을 증명하지 않습니다.

최신 PDF의 “모델만 검색하는 시연안”은 미정입니다. 선택하려면 Google 명세의 항공사 필터·applied_scope·R05 검증도 함께 합의해야 하며 이번 문서 변경에서는 필터를 수정하지 않습니다.

### 4. business-mcp 조회 권한 · C04

현재 V4의 `mro_business_reader`는 repair_requests/assignments를 읽지 못합니다. B01 접수 조회와 B02 배정 이력에 필요한 열/뷰를 나현이 정하고 Backend가 다음 Flyway PR로 권한을 추가해야 합니다. maintenance_history 조회는 이미 가능합니다. users나 개인 식별 열을 전부 공개하거나 RAG 계정을 업무 조회에 사용하지 않습니다.

## 팀 시드 반영 사항

아래 수치·날짜 검사는 기존 9d38555 기준으로 수행한 기록입니다. 최신 PDF도 1,499건과 JASC 컬럼을 유지하지만, 이번 규약 검토에서 최신 CSV 전체 검사를 다시 실행하지는 않았습니다.

- aircraft 20대: KAL 7 / AAR 7 / JNA 6. 상세 모델 10종.
- maintenance_history 1,499건: 모든 jasc_code가 4자리이며 현재 548개 코드표에 존재합니다. V2 컬럼은 nullable이므로 미래 null을 추정으로 채우지 않습니다.
- ATA 2자리와 JASC 4자리를 별도로 보존합니다. S21의 3230/3242, S22의 2160/2150을 앞 두 자리로 합치지 않습니다.
- scenarios.md는 S01~S22 + V1~V6, 총 28건입니다. 이 저장소의 15개 테스트와 별개이며 28개 업무 시나리오 전체를 통과했다고 주장하지 않습니다.
- 실제 CSV reported_at 범위는 **2024-01-16T11:38:00Z~2026-10-19T10:52:00Z**로, 메시지의 2025-10-21~2026-10-20과 다릅니다. 시나리오 날짜 기준은 팀 시드 파일과 맞춰야 합니다.
- 업무 시드는 Backend SeedLoader가 소유합니다. RAG 적재기는 aircraft/maintenance_history를 복제하거나 덮어쓰지 않습니다.
