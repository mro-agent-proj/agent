# 2026-10-23 실제 서비스 평가의 MCP 사용 기록

정본은 [deliverables의 200건 평가 번들](https://github.com/mro-agent-proj/deliverables/tree/main/evaluations/2026-10-23-isolated-200)입니다.
여기에는 2026-10-26 공개한 기록의 읽는 방법만 둡니다. MCP 구현이나 DB는 변경하지 않았습니다.
실제 Backend·RabbitMQ·OpenAI·HTTP MCP·Docker DB로 수행한 1회 실행이며,
합성 FastMCP 서버를 사용하는 AI 클라이언트 단위 테스트와 구분합니다.

## 도구별 관찰

`full-checkpoint-audit.json`의 `node_tool_counts`를 합산했습니다.
LLM1 열은 `classify`와 `direct`를 합친 값이고, 마지막 열은 `finalize`입니다.

| 도구 | LLM1·직접 확정 | LLM2 조사 | LLM3 검토 | 최종 실행기 |
| --- | ---: | ---: | ---: | ---: |
| B01 `get_request_context` | 316 | 84 | 0 | 29 |
| B02 `get_aircraft_history` | 345 | 0 | 0 | 0 |
| B03 `search_departments` | 316 | 176 | 174 | 29 |
| R01 `search_jasc_codes` | 0 | 77 | 0 | 0 |
| R02 `search_sdr_cases` | 0 | 84 | 0 | 0 |
| R03 `search_amm_references` | 0 | 0 | 0 | 0 |
| R04 `search_technical_docs` | 0 | 0 | 0 | 0 |
| R05 `get_evidence` | 0 | 0 | 0 | 97 |
| 확장 `get_sdr_amm_references` | 0 | 0 | 0 | 29 |

- 체크포인트 trace 증가분입니다. 서버 HTTP 로그의 전체 요청·재시도 수가 아닙니다.
- LLM2의 `DUPLICATE_CALL` 1건은 내부 차단 기록이므로 표의 MCP 호출에서 제외했습니다.
- LLM2 B02·HISTORY 기록은 0건입니다. 차단은 AI의 상태 격리·도구 gateway에서 수행하며, MCP JWT가 LLM 단계별 권한을 구분하는 것은 아닙니다.
- R03은 LLM 판단 도구에서 제외돼 있고 AMM은 확정 후 참고자료입니다. R04가 이번 실행에서 0회였다고 서버가 구현되지 않았다는 뜻은 아닙니다.
- B01·B03 및 최종 검증·첨부 호출에는 실행기 고정 호출이 포함됩니다. 모든 수치를 모델이 자율 선택한 호출로 해석하지 않습니다.

## 이 자료로 확인할 수 없는 것

도구 사용 횟수는 검색 적합도·Recall@K·분류 정확도를 측정하지 않습니다.
전체 평가 통과에는 LLM1·LLM2·LLM3 및 Backend 검증이 함께 영향을 줍니다.
RAG 성능이나 개선 효과는 별도 검색 평가와 동일 조건의 비교 실행으로 확인해야 합니다.
기존 README의 10월 21~22일 서버 로그 수치는 별개의 과거 실행입니다.

LLM 저장소의 [오프라인 재집계 도구](https://github.com/mro-agent-proj/LLM/tree/main/scripts/evaluation)로
저장된 결과와 감사 기록을 다시 대조할 수 있습니다. CI 합성 DB 테스트, 이 실구동 평가,
현재 소스의 신규 평가를 서로 같은 검증으로 표시하지 않습니다.
