# MCP 도구 계약

[Google 팀 명세](https://docs.google.com/document/d/1gyMiTHTtQQ329pOZlAJfNGpa3mmcJO0s-gdOF82bCpA)와 사용자 결정을 반영한 로컬 구현 스키마입니다. 팀 전체 최종 승인 스키마를 뜻하지 않습니다.

`mcp-tool-contracts.json`에는 읽기 도구 8개가 있으며, 실행 서버는 우리 담당 R01/R02/R03/R05 네 개만 등록합니다. 패키지의 `src/mro_rag/tool_contracts.json`과 문서 계약의 네 정의가 같은지 테스트합니다. 나머지 B01/B02/B03/R04는 이 서비스가 구현하지 않습니다.

쓰기 경계는 팀 PDF를 우선하여 B04/B05/B06/R06을 제거했습니다. 최신 PDF 본문 v0.3도 같은 제외 목록과 Google 도구 이름·근거 ID를 명시합니다. 이름·요청 범위·근거 ID는 Google 명세를 적용하고 R02 출처 필드는 source_locator, R05는 locator입니다. 이번 v0.3 갱신에서 공개 JSON 스키마는 변경하지 않았습니다. 변경 전 12개 카탈로그는 별도 원본 데이터 패키지 contracts에 보존하며 서버 등록에 사용하지 않습니다.

Google 원문 보존 JSON과 팀 PDF는 로컬 참고자료로 Git에 포함하지 않습니다. 출처 링크는 links.json에 있습니다. 내부 인증 전달 구현은 [Backend 안내](../../integration/backend/README.md), 공개 도구 동작은 [연결 계약](../INTEGRATION_CONTRACT.md)을 참고합니다.
