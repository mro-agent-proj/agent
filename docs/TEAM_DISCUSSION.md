# 팀 문서·실제 Backend 차이와 연결 사항

기준일 2026-10-21. 사용자 결정에 따라 쓰기 경계는 PDF, 요청 범위·근거 ID는 Google 명세, 환경 연결은 최신 Backend 저장소를 적용했습니다. 공유 문서 수정이나 팀 메시지 발송은 하지 않았습니다.

## 통합 전에 해결해야 하는 사항

| 항목 | 현재 확인한 문제 | 필요한 연결 |
|---|---|---|
| 요청 위임·스냅샷 | 실제 RunPayload에는 조건 버전·위임 사용자·색인 고정 정보 없음 | Backend가 검증 컨텍스트 게시 및 위임 JWT 발급. 공개 LLM 입력으로 권한 정보를 받지 않음 |
| 배정 전 근거 | AgentEventService ASSIGN 경로에 SDR/AMM ID·범위·버전 검증 없음 | Evidence.ref_id=evidence_id 연결, R05로 확인 후 배정. HISTORY는 업무 DB 별도 확인 |
| 항공사·정확 모델 매핑 | KAL/AAR/JNA와 원본 SDR operator_code 체계가 다름 | 검증 매핑 근거·버전 제공. 미확정은 NEEDS_INPUT, 전체 항공사/모델로 우회 금지 |

수정 위치·필드·통합 순서는 [Backend 안내](../integration/backend/README.md)에 정리했습니다. 위 연결이 끝나기 전에는 실제 접수→자동 배정까지 완료됐다고 볼 수 없습니다.

## 반영 완료하거나 기록할 차이

| 항목 | 적용 결과 |
|---|---|
| MCP 쓰기 충돌 | B04/B05/B06/R06 제외, 업무 변경 Backend·색인 갱신 운영 CLI로 분리 |
| PDF Alembic vs 실제 Flyway | 공유 DB용 V3 SQL 제공, 로컬 Alembic과 동일성·기존 V1/V2 공존 검증 |
| pgvector·모델 미정 | 사용자 지시에 따라 로컬 multilingual-e5-small/384차원/cosine 선택 및 전체 구축 |
| 업무 시드 규모 | 최신 Backend 기체 20대·이력 1,499건·28개 시나리오를 기준으로 반영. 구 패키지 업무 목업 미적재 |
| 시드 날짜 | 메시지는 2025-10-21~2026-10-20, 실제 CSV는 2024-01-16~2026-10-19. 파일 기준 사용 |
| 수리이력 JASC | V2 nullable varchar(4), 현재 1,499건 모두 유효 코드. ATA와 분리 |
| AMM 의미 | 실제 자료는 SDR 인용 참조·맥락, 정비 매뉴얼 본문이나 검증된 적용성 아님 |
| AMM ID | 참조번호는 여러 원본에 반복. EV-AMM-*를 근거 키로 보존하고 R05 원문 일치 검증 |
| SDR 제출일 | 원문 timestamp·UTC 시점·원문 날짜를 DB에 함께 보존, R02는 원문 날짜 |
| 조치 오프셋 | 원본 추출기는 action만 strip 적용. 오프셋을 바꾸지 않고 동일 추출 규칙으로 검증 |
| JASC 결측 | 정의 null 61개·2924 명칭 미확인 유지, 응답 warnings |
| 도구명 | R01/R02/R03/R05를 Google 기준으로 사용, R02 source_locator 적용 |

R04 기술자료·business-mcp·RabbitMQ 실행기·LLM2/3은 이번 구현 범위가 아닙니다. 28개 업무 시나리오는 해당 구성요소가 연결된 뒤 별도로 실행해야 합니다.
