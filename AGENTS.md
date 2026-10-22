# 프로젝트 작업 규칙

## 기준과 범위

- 최신 팀 규약은 로컬 `MRO Agent 팀 인터페이스 규약 v0.1 (3).pdf`의 **본문 v0.3, 29쪽**이다. 파일명·반복 머리말의 v0.1로 버전을 판단하지 않는다. 원문 해시와 대조 근거는 [규약 검토 보고서](reports/team-interface-v03-review.md)에 있다.
- 사용자 최신 지시를 우선한다. 팀 PDF는 역할·운영·쓰기 경계를, [Google MCP v1.0](https://docs.google.com/document/d/1gyMiTHTtQQ329pOZlAJfNGpa3mmcJO0s-gdOF82bCpA)은 공개 도구 이름·입출력·요청 권한·검색 범위·근거 ID를 정한다. 로컬 JSON이나 구현 제안을 팀 합의로 간주하지 않는다.
- 최신 사용자 지시에 따라 이 저장소는 DB·business-mcp(B01/B02/B03)·rag-mcp(R01~R05)와 FastAPI AI 서버 연결 구성을 담당한다. LLM1/2/3·FastAPI·RabbitMQ 실행기는 별도 LLM 저장소에 두며 여기로 복제하지 않는다. R04는 Backend V5 테이블(경님 전달본)을 읽는다. 실행 컨텍스트·인증 방식 확정은 별도 후속 작업이다.
- 팀 문서의 미정 항목은 미정으로 표시한다. JWT+컨텍스트 방식과 모델만으로 검색하는 시연 방식은 아직 합의되지 않았다. 기존 권한·범위 검사를 조용히 제거하거나 요청 ID를 권한으로 취급하지 않는다.

## DB와 데이터 소유권

- 공유 DB의 생성·계정·권한·마이그레이션·업무 시드·RAG 복원은 Backend가 소유한다. 공유 DB에 직접 DDL/적재를 실행하거나 Python Alembic을 추가 실행하지 않는다.
- 공유 스키마 기준은 Backend `src/main/resources/db/migration`이다. 적용된 V3/V4를 수정하지 않고 다음 Flyway 마이그레이션을 Backend PR로 제안한다. 우리 SQLAlchemy 모델·로컬 마이그레이션과의 차이도 함께 검토한다.
- 이 저장소의 Compose(55432), Alembic, ingest/embed는 격리된 데이터 제작·검증용이다. 팀 연결은 Backend DB(5432)를 조회한다. 포트 변경만으로 인증·컨텍스트 연결이 완료되지는 않는다.
- RAG 데이터는 로컬에서 제작·검증 후 스키마와 맞는 data-only dump, manifest, SHA256SUMS, verify.sql로 전달한다. 현재 전달본은 데이터 9개 테이블이며 런타임 `rag_request_contexts`는 제외한다. 공유 DB 복원은 Backend의 `scripts/rag-restore.sh`가 담당한다.
- MCP는 조회 계정만 사용한다. rag-mcp는 `mro_reader`, business-mcp는 `mro_business_reader`다. B01·배정 이력 조회에 권한이 부족하면 필요한 열/뷰를 Backend PR로 요청하며 소유자 계정으로 우회하지 않는다.
- 이미 적재된 DB를 새 덤프로 덮어쓰거나 `docker compose down -v`로 초기화하지 않는다. 데이터 갱신·기존 볼륨의 계정 보완은 Backend 담당 절차로 처리한다.

## 호출과 근거

- MCP는 읽기 전용이며 B04/B05/B06/R06을 등록하지 않는다. AMM은 SDR의 참조번호·인용 맥락으로 취급하고 실제 매뉴얼 절차로 표시하지 않는다.
- SDR/AMM의 `Evidence.ref_id`는 반환된 `evidence_id` 그대로다. HISTORY는 `MH-*`를 B02로 확인한다. AMM 번호나 source_id를 근거 ID로 대체하지 않는다. 동일 `evidence_group_id`의 파생 근거를 독립 근거로 중복 계산하지 않는다.
- v0.3에서는 AI 실행기가 `COMPLETED` 발행 전에 B02/R05로 근거를 검증하고 Spring이 상태 변경·배정을 실행한다. LLM의 자체 검토 문구를 실제 도구 검증으로 인정하지 않는다. Backend의 `VALIDATION_FAILED` 사유 처리 불일치는 [C03](reports/team-interface-v03-review.md#c03-근거-검증-실패-사유가-backend에서-변경됨)을 추적한다.
- 현재 구현의 위임 JWT·실행 컨텍스트·고정 release/index·버전 검사를 보존한다. 전달 방식 변경 시 요청 권한과 검색 스냅샷을 어떻게 유지하는지 계약과 테스트를 먼저 맞춘다. `repair_requests.version`과 `context_version`은 다르다.
- KAL/AAR/JNA를 원본 SDR 운항사에 임의 매핑하지 않는다. 항공사×정확 모델 범위를 유지하며 미확정은 `NEEDS_INPUT`이다. 모델만 검색하는 시연안은 Google 명세와의 변경 합의 전에는 구현하지 않는다.

## 후속 AI 연결

- Spring↔AI는 RabbitMQ를 사용한다. 큐·익스체인지는 Spring만 선언하며 실제 AI 연결 시 `AI_MODE=rabbit`을 사용한다. AI가 업무 DB를 직접 변경하지 않는다.
- 실행 큐 `mro.agent.run`, 이벤트 큐 `mro.agent.events`, exchange `mro.agent`, routing key `run.requested`/`agent.event`를 사용한다. 소비 prefetch=1, 완료/실패 이벤트 발행 확인 후 ack, 복구 불가 시 reject(requeue=false)다. 재전송은 같은 event_id를 유지한다.
- 5분 처리 한도, MCP 호출 10초·재시도 최대 1회, LLM2 재조사 최대 2회가 현재 팀 기본값이다. 재시도는 재시도 가능한 오류에만 적용한다. 스키마 캐시와 요청별 인증 헤더를 분리하고 다른 run의 토큰을 공유하지 않는다.

## 변경과 검증

- GitHub 변경은 별도 브랜치와 PR로 제출한다. main 직접 push·자동 merge를 하지 않는다. 팀 메시지는 초안 작성과 실제 발송을 구분한다.
- PDF·Google 원문·원본 데이터·덤프·모델 가중치·비밀값은 Git에 넣지 않는다. 이미 전달한 덤프/체크섬을 덮어쓰지 않는다.
- 문서 변경은 출처·링크·현재 코드와의 일치를 검증한다. 코드/DB 변경은 관련 계약·범위·권한·버전 검사를 실행한다. 로컬 RAG 검증, Backend 저장소 반영, 공유 DB 실제 복원, 전체 애플리케이션 통합을 별도 상태로 보고한다.
