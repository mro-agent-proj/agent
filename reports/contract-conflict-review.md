# MCP 로컬 초안과 팀 문서 충돌 점검

이 문서는 구현 전 충돌 점검의 기록입니다. 최신 구현·실제 Backend 연결 상태는 [구현 검토](implementation-review.md)를 따릅니다.

점검일: 2026-10-21. 대상: `docs/spec-v1/mcp-tool-contracts.json`의 12개 도구, 팀 PDF v0.1, 현재 Google MCP 명세 v1.0. Google 문서를 다시 읽었으며 이전 확인 시점과 같은 revision이었습니다.

## 적용 기준

팀 문서 > 로컬 보존본. 팀 문서 안에서는 환경·상태·읽기 전용 경계는 PDF, 도구 이름·입출력·검색 범위는 PDF가 우선하도록 지정한 Google 명세를 사용합니다. 로컬 inputSchema/outputSchema는 실행 전 대조해야 할 초안입니다. 원본 JSON을 통째로 tools/list에 등록하지 않습니다.

## 직접 충돌 1건과 통합 시 중대한 위험 2건

1번은 로컬 초안과 팀 PDF의 직접 충돌이며 Google 문서에도 같은 충돌이 있습니다. 2·3번은 로컬 초안의 결함이 아니라, Google 명세와 다른 PDF의 간략 예시를 그대로 구현할 때 발생하는 위험입니다.

1. **쓰기 경계 충돌**: 로컬·Google에는 B04/B05/B06/R06 쓰기 도구가 있고 PDF는 MCP 두 서버 모두 읽기 전용입니다. 전부 등록하면 Backend 전용 상태 변경과 별도 실행 경로가 생깁니다. 현재 네 RAG 조회 도구만 구현하고, 배정·인계·정비팀 응답은 Backend에서 처리합니다. 색인 적재는 운영 스크립트로 분리하며 R06 공개 등록은 하지 않습니다. 이는 팀 PDF 우선 적용입니다.
2. **요청 범위·권한 누락 가능성**: PDF 간략 표는 모델/ATA 직접 입력이고 request_id/context_version이 없습니다. 이 표만 구현하면 다른 요청·운항사·기종의 자료를 반환하거나 이전 요청 조건으로 검색할 수 있습니다. Google 명세의 request_id/context_version과 서버가 검증한 범위·인증을 유지합니다. Backend→MCP의 신뢰 가능한 전달 방식은 영호·나현님과 연결 전에 확정해야 합니다.
3. **근거 ID 충돌·오검증 가능성**: PDF의 AMM-챕터-섹션-주제 ID는 여러 원본에 반복될 수 있고 get_document(id)만으로는 접수 범위·버전을 검증할 수 없습니다. Google R05의 evidence_id+request_id+context_version으로 검증합니다. Evidence.ref_id가 어떤 키를 담는지는 나현·영호님과 일치시켜야 합니다. 단순 AMM 번호를 고유 근거 ID로 사용하지 않습니다.

R01/R02/R03/R05 핵심 입력·동작은 로컬 초안과 Google 문서가 일치합니다. AMM을 실제 절차 본문으로 취급하지 않는 것 역시 Google 명세를 따르므로 새 승인이 필요한 변경이 아닙니다. 도구 이름, 상태 표기, 재시도 시작값, R02 source_locator 대 locator 등은 팀 기준에 맞춰 정리하며 별도 치명적 문제로 확대하지 않습니다. 단, 로컬 엄격 스키마를 무수정 적용하면 필드명 차이로 검증 실패하므로 구현 전 실행용 스키마에 반영합니다.

## 시행 상태

[팀 연결 계약](../docs/INTEGRATION_CONTRACT.md)과 [보존본 안내](../docs/spec-v1/README.md)에 위 기준을 반영했습니다. 현재 구현 대상은 R01 search_jasc_codes, R02 search_sdr_cases, R03 search_amm_references, R05 get_evidence입니다. AMM은 SDR에 기록된 참조번호와 인용 맥락을 반환하며 실제 정비 절차나 적용성 확인 결과로 표현하지 않습니다.

영호·나현님과 연결 전에 확정할 사항은 [팀 논의 목록](../docs/TEAM_DISCUSSION.md)의 D4(인증된 접수 범위·버전 전달)와 D3(Evidence.ref_id와 evidence_id 연결)입니다. 통합 검증에서는 범위 밖 근거와 오래된 버전을 거부하고, 같은 AMM 번호라도 서로 다른 원본의 근거를 구분해야 합니다. 일반 필드명 차이는 팀 명세에 맞춰 실행용 스키마를 만드는 과정에서 처리합니다.

2026-10-21 사용자 결정 반영: 로컬 mcp-tool-contracts.json에서 B04/B05/B06/R06을 제거해 읽기 도구 8개만 남겼고 R02 locator를 Google 명세의 source_locator로 수정했습니다. 요청 권한·검색 범위는 Google 명세를 적용합니다. 근거 ID도 Google evidence_id로 진행하며 Backend Evidence.ref_id 연결과 R05 원본·범위·버전 검증을 통합 완료 조건으로 둡니다.

변경 전 12개 도구 JSON은 data/source/airbus-mcp-data-v1/contracts/mcp-tool-contracts.json에 그대로 보존했습니다. 실행 서버·DB 설정과 팀 Google 문서·PDF는 변경하지 않았으며 팀원에게 메시지를 보내지 않았습니다.

## 근거

- 별도 제공 팀 PDF v0.1 1쪽 도구 명세 우선, 4~5쪽 읽기 전용·초안 도구 표, 7쪽 근거 검증.
- [Google MCP 명세](https://docs.google.com/document/d/1gyMiTHTtQQ329pOZlAJfNGpa3mmcJO0s-gdOF82bCpA) 3~5절 범위·공통 반환·R05, 8절 인증.
- [로컬 입력·출력 규격 보존본](../docs/spec-v1/mcp-tool-contracts.json) 카탈로그 status 및 각 도구 annotations/inputSchema.
