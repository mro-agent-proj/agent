# MCP 도구 상세와 검색 방식

README의 요약 표를 보완하는 상세 문서입니다. 도구 계약 원본은 [spec-v1/mcp-tool-contracts.json](spec-v1/mcp-tool-contracts.json)입니다.

## 도구 9개

| ID | 도구 | 하는 일 | 주요 입력 | 읽는 데이터 | 근거 ID | 누가 부르나 |
| --- | --- | --- | --- | --- | --- | --- |
| B01 | `get_request_context` | 접수 내용·현재 상태와, backend가 이 실행에 등록한 **실제 검색 범위**(`context_version`·`model_key`·운항사·고정 조건). rag-mcp와 같은 범위 | `request_id` | 뷰 `agent_request_context`, `agent_run_context` (V9) | - | AI 실행기: 시작·최종 확정 직전 |
| B02 | `get_aircraft_history` | 같은 기체의 이전 수리이력. 접수 시각 이전만 | `occurred_from`·`occurred_to`, `limit` | `maintenance_history` 1,600 (v2 시드) | `MH-*` | LLM1 이력 비교, LLM3 추가 확인, 최종 검증. LLM2는 AI 실행기에서 차단 |
| B03 | `search_departments` | 정비 부서와 담당 ATA. JASC 후보 앞 두 자리로 순위만 조정 | `query`, `candidate_jasc` | `departments` 8 | - | AI 실행기: 단계마다 고정 확인 (도구 예산 제외) |
| R01 | `search_jasc_codes` | JASC 코드 후보와 공식 명칭·정의 | `query`(영어) 또는 `codes` | `jasc_codes` 548 | - | LLM2·LLM3가 골라서 |
| R02 | `search_sdr_cases` | Backend 허용 모델 범위의 SDR 사례. 단어·벡터 하이브리드 (한국어 질의 가능) | `query`, `candidate_jasc`, `search_mode` | `sdr_cases` 71,741, `rag.sdr_embeddings` 71,741 | `EV-SDR-*` | LLM2·LLM3가 골라서 |
| R03 | `search_amm_references` | AMM 번호로 그 번호가 인용된 SDR 문장 조회 (번호 조회만, 의미 검색은 V8에서 제거) | `amm_reference_exact` | `amm_references` 35,396 | `EV-AMM-*` | 쓰지 않음 (LLM 도구 목록에서 제외) |
| R04 | `search_technical_docs` | Airbus Safety First 정비 기사 의미 검색 (OpenAI 임베딩) | `query`, `system_keywords`, `include_unknown_applicability` | `tech_documents` 61, `tech_chunks`·`rag.tech_embeddings` 496 | `EV-DOC-*` | LLM2·LLM3가 골라서 |
| R05 | `get_evidence` | R02·R03·R04에서 받은 근거의 원문과 인용 위치 재확인 | `evidence_id` | `evidence` 107,137, `tech_chunks` | 입력한 ID | LLM2·LLM3, 최종 근거 검증 |
| 확장 | `get_sdr_amm_references` | 판단에 쓴 SDR 근거마다 그 원문에 인용된 AMM 번호와 인용 문장 조회 | `evidence_ids`(EV-SDR-* 1~10개) | `evidence`, `amm_references` | `EV-AMM-*` | 배정 제안 확정 후 실행기 (LLM이 고르지 않음) |

모든 도구는 `request_id`(토큰과 같아야 함)를 받고, rag-mcp 도구는 `context_version`도 받습니다. 검색 도구는 `limit`과 `cursor`로 페이지를 넘깁니다.

**AMM은 판단 근거가 아니라 배정 후 참고 정보로 다룹니다.** `get_sdr_amm_references`는 검색이 아니라 조회입니다. LLM2가 근거로 쓴 SDR에 딸린 AMM 번호를 정비팀 화면에 "참고 AMM"으로 붙이는 용도입니다. R03 `search_amm_references`는 팀 명세에 있는 도구라 이름은 남기되 **AMM 번호 조회만** 합니다(Backend V8에서 의미 검색용 `amm_chunks`·`amm_chunk_references`·`rag.amm_embeddings` 제거). LLM2·LLM3 도구 목록에서는 빠져 있습니다. R03 입력 축소와 확장 도구는 아직 `docs/spec-v1`에 없는 제안이라 팀 합의가 필요합니다(테스트의 `SPEC_DEVIATIONS`, `LOCAL_EXTENSIONS`).

서비스 200건 실행에서는 LLM2가 R01 77회·R02 84회를 호출했고, 최종 실행기가 R05 97회와 SDR→AMM 조회 29회를 호출했습니다. R04 호출은 관찰되지 않았습니다.

[9개 도구의 단계별 사용 표와 확인 범위](LIVE_EVALUATION_20261023.md),
[200건 결과·감사 기록 정본](https://github.com/mro-agent-proj/deliverables/tree/main/evaluations/2026-10-23-isolated-200)을 함께 확인할 수 있습니다.

B02 차단은 LLM 저장소의 LangGraph 입력 격리·도구 gateway에서 수행합니다. MCP 서버는 B02를 유지하며 LLM1/LLM3/최종 검증에 제공합니다. MCP 토큰은 서버(business/rag)만 구분합니다.

도구 제목과 입력 스키마는 [`spec-v1/mcp-tool-contracts.json`](spec-v1/mcp-tool-contracts.json)과 일치해야 하고, 응답은 같은 파일의 출력 스키마로 검증합니다(테스트가 확인). 도구 설명(description)은 AI가 도구를 고를 때 읽는 문구라서 검색 요령을 여기에 적습니다.

## 검색 방식

- SDR·JASC 원문은 영어입니다. R01과 R02의 단어 검색 쪽은 영어 핵심어 2~5개가 좋습니다(예: `brake vibration taxi`, `FLAP SYS 1 FAULT`). 단어 검색에서 한국어 단어는 무시됩니다.
- **R02 하이브리드** (`explore` 모드):
  - **방식**: 같은 접수 범위에서 단어 검색 상위 50건과 의미 검색 상위 50건을 뽑아 순위를 합칩니다(Reciprocal Rank Fusion, k=60). 결과의 `score`는 합친 순위 점수이고 `score_kind`는 `fusion`입니다.
  - **페이지**: 후보 집합은 각 검색 상위 50건의 합집합(최대 100건)으로 고정합니다. `next_cursor`는 이 순서 안에서만 이동합니다. 후보 상한에 도달하면 `FUSION_WINDOW` 경고를 반환하므로 더 넓은 탐색은 질의를 구체화해 새로 시작합니다. 순위 또는 벡터/단어 검색 방식이 바뀌면 이전 커서는 `INVALID_CURSOR`로 거부합니다. 배포 전 발급된 R02 커서도 새 질의로 다시 받아야 합니다.
  - **의미 검색 데이터**: SDR 원문 임베딩(Backend V7 `rag.sdr_embeddings`, OpenAI `text-embedding-3-large` 1024차원)을 씁니다. 질의도 같은 모델로 임베딩하며, rag-mcp 환경변수 `RAG_OPENAI_API_KEY`가 필요합니다(질의 한 번에 약 $0.000003).
  - **단어 검색으로 돌아가는 경우**: 키가 없거나, 임베딩이 복원되지 않았거나, 임베딩 호출이 실패하면 지금처럼 단어 검색만 합니다. 이때 `VECTOR_UNAVAILABLE: …` 경고를 남깁니다. `search_mode="exact"`도 단어 검색(구절 일치)만 씁니다.
  - **효과** (정확한 기종 범위, 평가셋 `backend/data/eval/sdr`):
    - SDR 원문 영어 400: 상위 5건 ATA 일치 52% → 68%
    - 증상 영어 200: 59% → 74%
    - 증상 한국어 200: 28% → 64% (결과 없음 59건 → 0건)
  - **속도**: 질의당 중앙값 0.42초입니다.
  - **임베딩 다시 만들기**: `scripts/embed_openai.py`가 비어 있는 행만 임베딩하고(SDR 약 $0.6, 기술자료 1센트 미만), Backend 전달본(`sdr-embeddings_v1`, `airbus-safety-first_v2`, `sdr-jasc-amm_v2`)으로 묶습니다.
- R02 기본 모드(`explore`)와 R01은 **단어가 하나라도 맞으면** 많이 맞는 순으로 돌려줍니다. R02 `search_mode="exact"`는 구절 그대로 찾습니다.
- AMM 자료는 SDR에서 추출한 **참조번호와 인용 맥락**입니다. 실제 정비 매뉴얼 절차나 검증된 적용성이 아닙니다. 검색 점수는 진단 정확도나 정답 확률이 아닙니다.
- R04는 Backend V5·V8 테이블(`tech_documents`·`tech_chunks`·`rag.tech_embeddings`, Safety First 61편)을 R02와 같은 OpenAI 질의 임베딩으로 의미 검색합니다(키가 없으면 영어 단어 검색). 규칙은 다음과 같습니다.
  - **대상**: 정비(`maintenance`) 기사 23편만 검색합니다.
  - **기종군**: 접수 기종의 기종군(예: A320214 → A320 계열)이나 "all Airbus" 같은 전 기종을 언급한 기사만 돌려줍니다. 다른 기종군만 언급한 기사는 뺍니다.
  - **적용 범위 미상**: 기종을 언급하지 않은 기사와 `unknown` 문단은 `include_unknown_applicability=true`일 때만 참고로 포함합니다.
  - **운항사 필터**: 공개 자료라 적용하지 않습니다(`operator_filter_applied=false`).
  - **스냅샷**: `snapshot_ids`는 기사 버전과 임베딩 모델에서 만든 `TECH-*` 하나이며, R04와 R05(EV-DOC)가 같은 값을 돌려줍니다.
  - 기사는 계통 이해·점검 포인트 참고 자료이며 현재 기체의 정비 절차나 적용성 확인이 아닙니다.
