# 팀 연결 계약 · DB/RAG 검토안

기준일: 2026-10-21. **확정**은 사용자 지시·팀 PDF에 명시된 값, **명세**는 기존 MCP v1.0 계약, **제안**은 이번 설계, **미결정**은 팀 연결 전 확인할 값입니다. 이 문서는 공유 규약 변경이나 팀 승인을 대신하지 않습니다.

## 기준

- 사용자 최신 지시: 전체 흐름 유지, DB·MCP 우선, LLM2·LLM3 보류. JASC 필수 포함.
- 팀 인터페이스 PDF v0.1(별도 제공 원문): 환경·담당·읽기 전용 MCP·상태 전이. 1, 4~7, 10쪽.
- [Google MCP v1.0](https://docs.google.com/document/d/1gyMiTHTtQQ329pOZlAJfNGpa3mmcJO0s-gdOF82bCpA): 도구 이름·입출력·요청 권한·검색 범위·근거 ID 기준. [로컬 JSON](spec-v1/mcp-tool-contracts.json)은 사용자 결정에 따라 쓰기 도구를 제거한 구현 초안입니다.
- **팀 문서가 로컬 보존본보다 우선합니다.** 쓰기 경계·상태는 팀 PDF를 따르고 환경은 사용자 최신 지시에 따라 실제 Backend 저장소를 반영하며, 도구 이름·입출력·검색 범위는 PDF가 우선하도록 명시한 Google 명세를 따릅니다. 로컬 JSON만의 추가 제약을 팀 합의로 강제하지 않습니다.
- 문서 간 충돌과 확인할 값은 [팀 논의 목록](TEAM_DISCUSSION.md)의 D 번호로 추적합니다. 필수 네 도구는 모두 읽기 전용이므로 설계를 진행할 수 있습니다.

## 확정 환경·책임

| 항목 | 기준 |
|---|---|
| 런타임·DB | RAG Python 3.12/SQLAlchemy async, PostgreSQL16/pgvector. 실제 Backend는 Spring Boot/Flyway/RabbitMQ |
| 스키마 변경 | 공유 DB는 Backend Flyway, 독립 로컬 검증 DB만 Alembic. 관계형 public, 임베딩 rag.amm_embeddings |
| MCP 연결 | business-mcp/rag-mcp, Streamable HTTP, langchain-mcp-adapters. 로컬 RAG 기본 http://localhost:8102/mcp |
| 쓰기 | Backend 저장·배정·업무 상태 변경. 조회 도구 읽기 전용. 적재·임베딩은 별도 운영 작업 |
| 키·시간 | 내부 UUID v4와 외부 업무 키 구분. 시각 UTC/API ISO 8601 Z. 날짜만 있는 값은 date |
| 우리 담당 | 데이터 설계·조회 로직·R01/R02/R03/R05 통합 자료 |
| 나현님 | MCP 클라이언트·도구 등록·연결 |
| 영호님 | Backend·인증·요청 범위·마이그레이션·배정 전 근거 검증 |

업무 enum 대문자 규칙을 MCP의 `explore`, `derived_amm_reference`, `not_verified` 등에 일괄 적용하지 않습니다. 도구 값은 명세를 보존합니다.

## 도구 계약

| 도구 | 명세 입력 | 주요 결과·동작 |
|---|---|---|
| R01 search_jasc_codes | request_id, context_version, query 또는 codes; limit 선택 | 코드·명칭·정의·출처·행별 codebook_version. 활성 코드표 전체 검색 |
| R02 search_sdr_cases | request_id, context_version, query; candidate_jasc/search_mode/limit/cursor 선택 | 사례·발췌·조치 후보·AMM 참조·근거. 항공사/정확 모델 필수 |
| R03 search_amm_references | request_id, context_version, query 또는 amm_reference_exact; 후보·수준·모드·페이지 선택 | AMM 참조 출현과 원본 SDR. query+번호이면 번호 일치 범위 안에서 증상 검색 |
| R05 get_evidence | request_id, context_version, evidence_id | SDR/AMM 원문·인용 위치·버전. 기체 목업 이력은 B02 대상 |

입력·출력은 팀 Google 명세를 기준으로 구현하며 로컬 JSON은 초안 참고자료입니다. 팀 명세와 다른 필드명·타입·추가 제약은 팀 기준에 맞춘 실행용 스키마로 별도 정리합니다. 예를 들어 R02의 출처 위치 이름은 팀 Google 문서의 source_locator를 우선하고, R05는 locator를 사용합니다. PDF가 도구 규격은 Google 명세 우선이라고 지정했으므로 search_sdr/search_amm/get_document 초안 대신 Google 도구명을 사용합니다. R04 및 R05 기술자료 분기는 담당 확정 후 확장합니다.

## 요청 범위 검증 기준과 전달 구현안 · D4

2026-10-21 사용자 결정으로 Google 명세를 적용합니다. 적용 기준은 확정이며 아래 내부 전달 경로의 실제 연결만 남아 있습니다.

LLM 입력으로 operator/tenant/role을 받지 않습니다. request_id는 권한이 아닙니다. RAG 서버가 인증된 서비스 호출의 사용자/tenant 위임을 검증하고 Backend resolver 또는 권한 제한 내부 조회에서 다음 값을 받도록 제안합니다. 이 서버는 위임 HS256 JWT와 Backend 소유 rag_request_contexts 조회를 구현했습니다. Java Backend 연결은 남아 있으며 [구체적 전달 계약](../integration/backend/README.md)을 따릅니다.

| 신뢰할 컨텍스트 | 의미 |
|---|---|
| caller/tenant/role 또는 동등 권한 정보 | 해당 접수 접근 권한. 서비스 인증만 있고 사용자 위임이 없으면 불충분 |
| request_id, context_version | 접수 검색 조건 버전. 불일치 CONTEXT_VERSION_CONFLICT |
| operator_code, model_key | 검증된 매핑으로 결정한 SDR 범위. 미확정 NEEDS_INPUT |
| locked_filters | 사용자 고정 JASC·발견일. 후보 JASC와 분리 |
| release_key, index_version_ids, mapping_revision | 실행에 고정한 데이터·색인·매핑 버전 |
| run_id 또는 동등 실행 식별자, trace_id | 같은 접수 버전의 동시 실행도 구분 |

이는 내부 계약이며 공개 MCP inputSchema에 새 인자를 추가하지 않습니다. Backend가 실행 시작 시 버전을 고정하고 RAG는 재개·페이지 이동·R05에서도 이를 사용합니다. 호출마다 최신 활성 색인으로 변경하지 않습니다. 고정 버전이 사라졌으면 INDEX_VERSION_UNAVAILABLE입니다. 인증 없는 스텁은 개발 테스트용이며 통합 완료로 인정하지 않습니다.

## 공통 반환

structuredContent는 status, request_id, context_version, trace_id, applied_scope, snapshot_ids, counts, items 또는 result, next_cursor, warnings, missing_fields, error를 도구 스키마대로 반환합니다. 보존 payload 전체를 반환하지 않습니다.

- NO_DATA: 기본 operator×정확 모델×snapshot 내 해당 자료 0건. JASC는 활성 코드표 자체의 자료 없음.
- NO_MATCH: 기본 자료는 있으나 고정 필터·조회 조건을 만족하지 않음.
- NEEDS_INPUT: 접수 범위 미확정. 접수 단계 422 보완 응답과 별개이며 도구가 상태를 변경하지 않음.
- ERROR: 색인 미준비·버전 충돌·권한/입력 오류·연결 장애. isError=true, 인증은 전송 계층 거부 가능.
- count 미계산은 null. 상위 k개 수를 전체 건수로 표시하지 않음.
- R01은 항공사/모델 필터 적용 플래그 false. 요청 권한·버전 검증은 유지.
- R03 번호만 조회는 관계형 자료로 가능. 벡터 준비 전 query-only/query+번호는 INDEX_NOT_READY. 조용히 번호만 검색하고 성공으로 표시하지 않음.
- cursor는 위변조 방지, 범위·질의·필터·색인 버전 결합 필요. cursor의 범위를 권한으로 신뢰하지 않음.

## 근거 ID 기준과 Backend 연결 · D3

case_id, distilled_record_id, chunk_id, evidence_id는 별도 키입니다. AMM 번호는 근거 PK가 아닙니다. 기존 EV-SDR-*/EV-AMM-*를 보존하고 release+evidence_id로 조회합니다. Backend는 존재 여부뿐 아니라 접수 권한·범위·고정 버전을 확인합니다.

Google 기준의 evidence_id로 DB·MCP 작업을 진행할 수 있습니다. Backend의 SDR/AMM Evidence.ref_id에는 검색 결과의 evidence_id를 그대로 전달하는 연결안을 사용하고 source ID는 별도 보존합니다. 이는 우리 구현 기준이며 팀 Backend가 이미 이 방식으로 구현되었다는 뜻은 아닙니다. MCP 응답에 임의 id를 추가하지 않고 소비자 내부 어댑터에서 변환합니다. sdr→SDR, derived_amm_reference→AMM도 소비자 내부 변환입니다. HISTORY는 B02로 검증합니다.

통합 완료 조건은 R02/R03의 evidence_id로 R05에서 동일 원본·인용 위치를 재조회하고, 다른 접수 범위·권한·고정 버전의 근거는 거부하는 것입니다. 같은 AMM 번호가 여러 원본에 있어도 근거 ID는 구분해야 합니다. 이 검증 전에는 호환성이 완료되었다고 판단하지 않습니다.

## 운영 연결

공유 스키마는 Backend Flyway로 통합하고 RAG 적재는 별도 운영 명령으로 실행합니다. Backend 업무 시드는 기존 SeedLoader가 소유합니다. 우리 로컬 폴더가 팀의 최종 저장소 구조라고 가정하지 않습니다. PDF의 10초·재시도 1회는 시작값으로 삼되 retryable 오류에만 적용하도록 제안합니다. 대량 임베딩은 조회 요청 중 생성하지 않습니다. 버전별 자료·색인을 준비한 뒤 활성 포인터를 전환하고, 실행 중인 이전 스냅샷은 유지하는 운영안을 [DB 설계](DB_DESIGN.md)에 기술합니다.
