# 팀 인터페이스 v0.3 변경·충돌 검토

검토일: 2026-10-21. **RAG 테이블·근거 ID·네 도구를 다시 만들 필요는 없다. 작업 책임과 실행 안내를 갱신하고, 인증/검색 범위/실패 사유/업무 조회 권한을 팀 통합 전에 정리해야 한다.** 이번 변경은 작업 규칙·문서만 반영하며 서비스 코드, 공유 DB, 외부 팀 문서는 수정하지 않는다.

## 확인한 기준

| 자료 | 식별 정보 |
|---|---|
| 최신 PDF | `MRO Agent 팀 인터페이스 규약 v0.1 (3).pdf`, 본문 **v0.3**, 29쪽. 생성 2026-10-21 05:11:40 UTC |
| 최신 PDF SHA-256 | `123258608c19ee40d4c754ab73e45f94b6651ffa3edb153b1b963b0a70315b6f` |
| 이전 PDF | `MRO Agent 팀 인터페이스 규약 v0.1.pdf`, 본문 v0.1, 11쪽 |
| 이전 PDF SHA-256 | `dbf7ceefb6372f98a6adfcbc9b8364f048b927b2beecd6839ecae10ffbfde9ec` |
| Google MCP | [명세 v1.0](https://docs.google.com/document/d/1gyMiTHTtQQ329pOZlAJfNGpa3mmcJO0s-gdOF82bCpA), 연결 도구로 재조회. 제공 수정 시각 2026-10-21 00:06:06 UTC |
| Agent 구현 | [5cffa21](https://github.com/mro-agent-proj/agent/tree/5cffa2162227fe26bcdf3913ddf8a5ad52bde5a1) |
| Backend 구현 | [0fc8caf](https://github.com/mro-agent-proj/backend/tree/0fc8caf575b5a1a89e8fbdaa9989866f5654d9ac), RAG 복원 PR #4 병합 후 |

PDF 페이지 번호는 파일의 1~29쪽이다. 파일명과 반복 머리말의 v0.1은 본문 버전과 다르다. PDF 전체 텍스트를 추출하고 접속표·책임·도구 목록·미정 항목은 1·2·21·22·29쪽 이미지로도 확인했다. PDF 추출문·페이지 이미지·Google 원문·Backend 읽기 사본은 로컬 `artifacts/spec-review-20261021/`에 보관하며 Git에는 포함하지 않는다.

## 이전 규약에서 바뀐 부분

| 항목 | 이전 v0.1 | 최신 v0.3 | 우리 작업에 적용 |
|---|---|---|---|
| 서버·마이그레이션 | Python 단일 저장소, FastAPI/Alembic | Spring(Java 21) + Python AI, 공유 Flyway, PostgreSQL 16+pgvector (15~17쪽) | 공유 DB 기준을 Backend Flyway로 고정. 로컬 Alembic은 격리 검증용 |
| DB·데이터 운영 | Backend 마이그레이션 중심 | DB·계정·업무 시드·RAG 복원까지 Backend 소유, 직접 적재 금지 (1~2·29쪽) | 우리 작업은 덤프 제작·전달. 공유 DB ingest/embed 실행 안내 제거 |
| 실행 경로 | Backend가 그래프·라우팅 수행 | Spring↔AI RabbitMQ+DLQ, AI가 LLM1~3·라우팅 수행 (17~21쪽) | 후속 AI 실행기 계약으로 기록. 현재 LLM2/3 보류 유지 |
| 상태·완료 처리 | 그래프/Backend 중심 | AI는 이벤트 발행, 상태·배정은 Spring만 수행. 처리 한도 5분 (18~19쪽) | 실제 연결은 AI_MODE=rabbit. 큐 재선언 금지, 완료 이벤트 후 ack |
| 근거 검증 주체 | 배정 시 Backend가 DB 대조 (구 4쪽) | AI가 COMPLETED 전 MCP로 대조 (21쪽) | AI 실행기의 B02/R05 검증으로 담당 수정. Spring의 R05 직접 호출을 확정 요구로 쓰지 않음 |
| 도구·반환 형식 | search_sdr/search_amm/get_document, 공통 id 초안 | Google 이름 B01~B03/R01~R05, structuredContent·상태 구분 (22~23쪽) | 기존 R01/R02/R03/R05 및 쓰기 도구 제외와 일치 |
| 근거 ID·AMM 의미 | SDR-* / AMM-번호 예시, AMM 절차 조각 | EV-SDR-* / EV-AMM-* 그대로, AMM 인용 맥락, 그룹 중복 제외 (20·23쪽) | 현재 evidence_id와 evidence_group_id 유지. 재해싱·번호 ID 변환 불필요 |
| DB 스키마·계정 | 상세 RAG 구조 없음 | V1~V4와 RAG 10개 테이블·분리 조회 계정 명시 (2~15쪽) | 현재 V3와 호환. rag_request_contexts 사용 방식은 여전히 미정 |
| 업무 이력 | ATA 중심 | maintenance_history.jasc_code 추가, 1,499건 (4·24쪽) | ATA 2자리/JASC 4자리 구분 유지 |

## 호환 확인과 이미 해결된 항목

- Backend V3는 이 저장소의 SQL 앞에 출처 주석 2줄만 추가한 파일이다. 주석 2줄을 제외한 내용은 완전히 같다. PDF의 RAG 구조는 해당 V3를 채택하므로 이번 규약으로 인한 컬럼 재설계·덤프 재생성은 필요하지 않다. [Backend V3](https://github.com/mro-agent-proj/backend/blob/0fc8caf575b5a1a89e8fbdaa9989866f5654d9ac/src/main/resources/db/migration/V3__rag_evidence.sql)
- pgvector 이미지와 V4의 조회 계정 권한이 Backend에 반영됐다. 기존 문서의 “이미지 변경·V3 통합 필요”는 완료 상태로 바꾼다. RAG의 public 9개+rag 1개 테이블 SELECT는 현재 서버의 읽기 경계와 맞는다. [Compose](https://github.com/mro-agent-proj/backend/blob/0fc8caf575b5a1a89e8fbdaa9989866f5654d9ac/docker-compose.yml), [V4](https://github.com/mro-agent-proj/backend/blob/0fc8caf575b5a1a89e8fbdaa9989866f5654d9ac/src/main/resources/db/migration/V4__db_readers.sql)
- **복원은 Spring 기동만으로 자동 실행되지 않는다.** 현재 구현은 Flyway 적용 후 Backend 담당자가 `./scripts/rag-restore.sh`를 실행하는 방식이다. 9개 RAG 데이터 테이블의 빈 상태, 전달본 체크섬, V3 호환, V4 적용 여부를 확인한다. 동일 release는 내용 검증 후 건너뛰고 일부 적재/다른 release는 중단한다. [복원 스크립트](https://github.com/mro-agent-proj/backend/blob/0fc8caf575b5a1a89e8fbdaa9989866f5654d9ac/scripts/rag-restore.sh)
- 기존 전달 ZIP `mro-rag-handoff-20261021-v1.zip`과 덤프는 변경하지 않는다. 덤프는 데이터 9개 테이블이며 실행 컨텍스트는 제외한다. 로컬 전달본 복원 검증과 **팀 공유 DB의 실제 복원 여부**는 다르며, 후자는 이번 점검에서 확인하지 않았다.

## 연결 전에 해결할 충돌

### C01 인증·검색 컨텍스트 전달 방식이 미정인데 접속표에는 인증 없음으로 표기됨

**근거:** PDF 2쪽 접속표에는 MCP 계정/비밀번호가 “없음”, 14·29쪽에는 `rag_request_contexts`+위임 JWT와 단순 request_id 전달 방식 중 선택이라고 적혀 있다. 반면 현재 [AuthMiddleware](../src/mro_rag/server.py)는 `/health` 외 HTTP 요청에 JWT가 없으면 401을 반환한다. [resolve](../src/mro_rag/service.py)는 토큰·컨텍스트·권한·조건 버전·release를 확인한다. Backend [RunPayload](https://github.com/mro-agent-proj/backend/blob/0fc8caf575b5a1a89e8fbdaa9989866f5654d9ac/src/main/java/com/skala/mro/domain/agent/dto/RunPayload.java)는 접수 ID·기체·모델·항공사·증상 5개 필드만 전달한다.

**영향:** 접속표대로 무인증 연결하면 초기 MCP 연결/툴 목록 조회부터 실패한다. JWT만 전달해도 실행 컨텍스트가 없으면 도구 호출이 거부된다. 22쪽의 “시작 시 도구를 한 번 로드해 캐시”를 요청별 토큰까지 공유하는 방식으로 구현하면 다른 run에 잘못 연결된다.

**팀에서 결정할 내용:** 내부 인증·권한 resolver·버전/스냅샷 전달 방식을 확정한다. JWT+컨텍스트는 현재 동작하는 **구현안**이지 최신 팀 최종 합의가 아니다. 다만 Google 명세 2·8절은 매 호출의 권한 확인, request_id≠권한, 고정 검색 범위를 요구하므로 단순 request_id 전달만으로 이를 충족했다고 볼 수 없다. 방식 확정 전에는 현 구현을 유지하고 인증을 제거하지 않는다. 담당: 세환·유빈·영호, MCP 클라이언트 나현.

### C02 정확한 모델만 검색하는 시연안은 기존 검색 범위와 충돌함

**근거:** PDF 29쪽은 KAL/AAR/JNA와 SDR 운항사 불일치를 인정하고 모델만 검색할지 미정으로 둔다. Google 명세 2절은 운항사×정확 모델 고정, 다른 항공사로 자동 확장 금지다. 현재 [scope](../src/mro_rag/service.py)도 operator_code와 model_key를 모두 필터링한다.

**영향:** KAL을 미국 SDR 운항사에 임의 연결하거나 항공사 필터를 빼면 다른 운항사 사례를 원래 접수 범위의 근거처럼 반환한다. 현재 규칙을 유지하면 미확정 매핑은 `NEEDS_INPUT`이므로 모델만 맞춰도 R02/R03의 팀 시연이 완성되지는 않는다.

**팀에서 결정할 내용:** 현 계약에 맞는 시연 데이터/접수 범위를 쓰거나, 별도 시연 검색 정책을 명시적으로 합의하고 Google 명세·applied_scope·R05·테스트를 함께 변경한다. DB에 operator_code를 가짜로 넣지 않는다. 담당: 세환·나현·영호.

### C03 근거 검증 실패 사유가 Backend에서 변경됨

**근거:** PDF 21쪽은 없는 근거 ID를 발견하면 `VALIDATION_FAILED`로 사람 검토에 넘기도록 한다. 그러나 Backend [AgentEventService.complete](https://github.com/mro-agent-proj/backend/blob/0fc8caf575b5a1a89e8fbdaa9989866f5654d9ac/src/main/java/com/skala/mro/domain/agent/service/AgentEventService.java)의 `HUMAN_REVIEW` 분기는 `RETRY_LIMIT`만 보존하고 나머지는 `AGENT_NEEDS_HUMAN`으로 바꾼다.

**정적 코드 재현:** AI가 `COMPLETED`의 payload를 `{"outcome":"HUMAN_REVIEW","review_reason":"VALIDATION_FAILED","summary":"근거 ID 검증 실패"}`로 보내면 저장 사유는 `AGENT_NEEDS_HUMAN`이 된다. `FAILED` 이벤트로 보내면 `SYSTEM_ERROR`가 되므로 대체 경로도 규약과 맞지 않는다. 실제 요청을 발행한 통합 재현은 하지 않았다.

**필요한 변경:** Backend PR에서 AI가 전달할 수 있는 사유 목록에 `VALIDATION_FAILED`를 반영하고 이벤트→저장 사유 테스트를 추가한다. AI는 검증 실패 시 ASSIGN을 발행하지 않는다. 현재 Spring의 ASSIGN 경로는 부서 유효성을 확인하지만 근거 자체를 확인하지 않으므로 새 규약의 AI 사전 검증이 생략되면 잘못된 근거를 그 경로에서 막지 못한다. 이번 문서 변경에서 Backend 코드는 수정하지 않았다.

### C04 B01·B02 배정 이력에 필요한 읽기 권한이 아직 없음

**근거:** PDF 4~6·22·29쪽에 권한 추가 예정으로 명시돼 있다. 실제 V4는 business 계정에 airlines/aircraft/departments/maintenance_history만 허용하고 repair_requests/assignments는 허용하지 않는다.

**영향:** B01의 접수 컨텍스트와 B02의 배정 이력은 현재 권한 그대로 직접 조회할 수 없다. 수리이력 테이블 조회는 가능하므로 B02 전체가 불가능하다는 뜻은 아니다.

**필요한 변경:** 나현이 필요한 열/뷰를 정하고 영호가 다음 Flyway PR로 권한을 추가한다. reporter_id, assigned_by_user_id, responded_by 등 개인 식별 열을 무조건 공개하지 않는다. rag-mcp 계정 권한을 넓혀 대신 조회하지 않는다.

## 후속 작업 순서

1. C01 내부 호출/컨텍스트 방식과 C02 시연 검색 범위를 합의한다. 현재 동작의 변경 여부가 결정되는 두 항목이다.
2. Backend가 기존 전달본을 `rag-restore.sh`로 복원·검증한다. 아직 전달하지 않았다면 ZIP/체크섬을 공유한다. 스크립트 병합만으로 공유 DB 적재 완료라고 표시하지 않는다.
3. C03 사유 처리와 C04 업무 조회 권한을 각각 Backend PR로 해결한다. C01에서 컨텍스트 방식이 확정되면 해당 연결도 추가한다.
4. 신뢰된 실행 메타데이터와 요청별 MCP 인증을 연결하고 R01/R02/R03→R05를 실제 팀 접수로 검증한다.
5. 후속 AI 실행기에 근거 사전 검증·RabbitMQ 이벤트를 연결한 뒤 업무 시나리오를 실행한다. LLM2/3 구현은 현재 사용자 보류 지시를 유지한다.

별도 후속 확인: 5분 처리 한도 내 모델 준비·재조사 실행 가능 여부, LLM1/라우팅 담당, R04 담당, langgraph 체크포인트의 별도 쓰기 역할/마이그레이션은 아직 결정되지 않았다. 이를 이유로 이번 RAG 스키마를 바꾸지는 않는다.

## 이번 검증 범위

구/신 PDF 텍스트와 주요 페이지 이미지, Google 명세 재조회, 고정 커밋의 Backend SQL·권한·DTO·이벤트 처리·복원 절차, 현재 MCP 인증·검색 필터를 대조했다. 문서 14개 범위의 로컬 링크 53개, JSON 구문, PDF 해시, V3 동일성, `git diff --check` 검사를 통과했다. 기존 전달 ZIP 및 SHA256SUMS의 6개 파일 해시도 일치한다. 변경은 문서 전용이므로 로컬 서비스 테스트는 재실행하지 않았다. 공유 Spring·RabbitMQ 종단 간 검증은 수행하지 않았다.
