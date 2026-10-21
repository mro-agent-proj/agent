# DB·MCP 작업 현황

기준일 2026-10-21, 팀 PDF 본문 v0.3·Backend 0fc8caf 반영. LLM2·LLM3 구현은 보류합니다. 작업 수행 규칙은 [AGENTS.md](../AGENTS.md), 변경 근거는 [규약 검토](../reports/team-interface-v03-review.md)를 따릅니다.

| 순서 | 작업 | 상태 |
|---|---|---|
| 1~3 | 범위·도구 계약·데이터 매핑·DB 설계 | 완료, 실제 Backend 변경 반영 |
| 4 | PostgreSQL16·pgvector·마이그레이션 | 로컬 완료. Backend pgvector/V3/V4 반영 확인, V3 DDL 일치 |
| 5 | JASC·SDR·AMM·근거 적재 | 전체 corpus 적재 및 재실행 중복 방지 검증 |
| 6 | 인증·접수 범위·버전·스냅샷 | 현재 JWT/컨텍스트 구현 완료. 최신 PDF에서 전달 방식·시연 범위는 미정(C01/C02) |
| 7~10 | R01/R02/R03/R05와 HTTP MCP | 네 도구 구현 및 실제 SDK 호출 검증 |
| 11 | 로컬 다국어 임베딩·pgvector 검색 | 31,678개 벡터 구축, 한국어/영어 검색→R05 검증 |
| 12 | 검토·재현 절차·저장소 인계 | 검증 덤프/manifest/체크섬/verify.sql 제작. Backend 복원 스크립트 반영, 공유 DB 실제 복원은 미확인 |

다음 팀 통합 순서는 ① 인증/컨텍스트·시연 검색 범위 결정 ② Backend 담당자의 덤프 복원·검증 ③ 실패 사유(C03)·업무 조회 권한(C04) Backend PR ④ 결정된 컨텍스트·MCP 클라이언트 연결 ⑤ AI 실행기의 COMPLETED 전 근거 검증과 업무 시나리오 실행입니다. 덤프 복원과 Backend 수정은 가능한 범위에서 병행할 수 있습니다. [구체적 연결 항목](../integration/backend/README.md)을 참고합니다.

로컬 DB·MCP 구성 완료와 팀 애플리케이션 전체 연결 완료를 구분합니다. 현재 검색 품질·진단 정확도 평가, 실제 매뉴얼 본문, business-mcp, RabbitMQ 실행기와 LLM 노드는 구현 범위 밖입니다.
