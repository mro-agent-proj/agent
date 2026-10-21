# 팀 논의 사항 · 인터페이스 v0.3

기준일 2026-10-21. 최신 PDF 본문 v0.3과 Backend `0fc8caf`를 대조했습니다. 상세 출처·영향·재현은 [규약 검토 보고서](../reports/team-interface-v03-review.md)에 있습니다. 이 문서는 팀 합의나 실제 Slack 발송을 대신하지 않습니다.

| 우선순위 | 항목 | 현재 상태와 필요한 결정 | 담당 |
|---|---|---|---|
| 1 · C01 | MCP 인증·실행 컨텍스트 | PDF 접속표는 인증 없음, 마지막 절은 방식 미정. 현재 서버는 JWT/컨텍스트 필수이므로 그대로 연결하면 실패. 권한·조건 버전·스냅샷을 유지할 전달 방식 확정 | 세환·유빈·영호, 클라이언트 나현 |
| 2 · C02 | 시연 검색 범위 | KAL/AAR/JNA와 SDR 운항사 불일치. 모델만 검색하는 시연안은 Google 항공사×모델 고정 규칙과 다름. 필터를 바꾸기 전에 명세와 시연 정책 합의 | 세환·나현·영호 |
| 3 · C03 | 근거 검증 실패 사유 | 새 규약은 AI가 COMPLETED 전에 B02/R05 검증. Backend는 HUMAN_REVIEW/VALIDATION_FAILED를 AGENT_NEEDS_HUMAN으로 변환하므로 사유 처리 수정 PR 필요 | 영호, AI 실행기 팀 |
| 4 · C04 | business-mcp 권한 | B01 접수·B02 배정 이력에 필요한 repair_requests/assignments 권한 없음. 필요한 열/뷰와 권한을 다음 Flyway PR로 추가 | 나현·영호 |

이미 맞춰진 항목은 pgvector 이미지, V3 RAG 구조, V4 RAG 조회 계정, R01/R02/R03/R05 명칭·반환 형식, EV-SDR/EV-AMM 근거 ID와 그룹 중복 제외입니다. 초기 덤프 복원 스크립트도 Backend에 반영됐습니다. **실제 공유 DB의 덤프 복원 여부는 별도 확인**해야 합니다.

RAG 데이터는 기존 검증 전달본을 사용합니다. 우리 저장소에서 공유 DB에 직접 적재하지 않습니다. Backend 담당자가 Flyway 적용 후 `scripts/rag-restore.sh`를 실행합니다. 새 데이터/스키마 변경은 덤프 재전달 또는 Backend Flyway PR로 처리합니다.

LLM1·라우팅 담당, 5분 처리 한도, R04, 체크포인트 쓰기 역할은 후속 협의 사항입니다. 현재 LLM2/3 보류와 DB·MCP 우선 범위는 유지합니다. 기존 시드 날짜·JASC·AMM 데이터 한계는 [Backend 안내](../integration/backend/README.md)와 [데이터 매핑](DATA_MAPPING.md)에 보존합니다.
