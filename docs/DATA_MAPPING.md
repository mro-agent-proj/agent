# SDR·AMM·JASC 데이터 매핑

기준일: 2026-10-21. 아래는 현재 패키지의 관측값과 PostgreSQL/MCP 변환 기준입니다. 전체 적재·원문 일치 검증을 완료했으며 실제 구조는 DB_DESIGN.md와 src/mro_rag/db.py를 따릅니다. 모든 상대 데이터 경로의 기준은 `data/source/airbus-mcp-data-v1/`입니다.

## 입력 계층과 보존

| 입력 | 용도 | DB 적재 정책 |
|---|---|---|
| canonical/raw_airbus_records.jsonl | 원본 84,989건, raw 내부 76컬럼. 바깥 case_id/source_file/record_no/hash는 패키지 메타데이터 | 검색용 case_id와 연결한 원본 raw를 sdr_cases.raw_record에 보존. 원본 전체 파일은 읽기 전용 유지 |
| runtime/sdr_cases.jsonl | 검색용 SDR 71,741건 | 관계형 조회 필드 + source_payload 전체 보존 |
| runtime/amm_references.jsonl | 참조 출현 35,396건 | AMM 참조 테이블, 원본 SDR에 연결 |
| rag/amm_chunks.jsonl | 임베딩 입력 청크 31,678개 | 청크와 참조 연결 35,396건. 실제 벡터 없음 |
| reference/jasc_codes.jsonl | 코드 548개 | JASC 테이블, null·출처·행별 버전 보존 |
| runtime.sqlite/evidence | SDR·AMM 근거 레지스트리 107,137건 | 입력 검산용. 적재 시 원본 JSONL에서 동일 ID·locator로 재구성 |
| package_manifest.json, provenance/source_manifest.json | 패키지 스냅샷·파일 해시·출처 | knowledge_releases 및 보존 파일 |
| evaluation/*, review/*, canonical 정제 전체 | 평가·격리·보존 자료 | 검색 테이블에 통째로 적재하지 않음 |
| business/* | 과거 목업 | 팀 Backend 시드와 별개. 자동 적재 금지 |

[전체 원본 컬럼 목록](mappings/raw-sdr-columns.csv)은 76개 이름의 대소문자·순서·채워진 건수·실제 값 예시를 보존합니다. `ButtLineFrom`과 `ButtlineTo`처럼 원본 표기가 다른 경우도 유지합니다. 빈 문자열, UNKNOWN/NONE/NA/UNK는 원본에서 구분해 보존하며, 정제 컬럼의 null 정책과 혼합하지 않습니다.

raw_record는 원본 raw 객체 전체이고 source_payload는 정제 레코드 전체입니다. JSONB는 키 순서·직렬화 바이트를 재현하는 저장소가 아니므로 원문 파일과 원래 hash를 별도 보존합니다. JSONB를 다시 직렬화해 원본 파일 해시와 비교하지 않습니다.

## 원본 SDR → 정제 → DB → R02

아래 컬럼명은 [DB 설계](DB_DESIGN.md)의 제안명입니다. 정제 입력의 모든 필드는 source_payload에도 남습니다.

| 원본 필드 | 정제 필드 | DB 컬럼/보존 위치 | MCP 처리 |
|---|---|---|---|
| OperatorControlNumber | operator_control_number, case_id | operator_control_number, case_id | case_id 반환, source_id도 사례 ID |
| OperatorDesignator | operator_code | operator_code text | 검증된 접수 범위와 일치해야 함. SubmitterDesignator로 대체하지 않음 |
| SubmitterDesignator | submitter_code | submitter_code nullable | 기본 R02 필드 아님, 원본 추적용 |
| AircraftMake | make_raw | make_raw | 원본 보존 |
| AircraftModel | model_raw, model_key | model_raw, model_key | 모델 키는 대문자/양끝 공백 정리값. 인증된 별칭이라고 주장하지 않음 |
| DifficultyDate | difficulty_date, difficulty_date_raw | difficulty_date date, 원문은 raw_record/source_payload | 발견일 필터 기본 축 |
| SubmissionDate | submission_date | submission_at timestamptz, submission_date date, submission_date_raw text | 현재 wire는 date. 원문 달력 날짜 반환 제안 D7. UTC 날짜로 무조건 절삭하지 않음 |
| JASCCode | reported_jasc | reported_jasc text, release 내 JASC FK | 보고된 코드이며 확정 원인 아님 |
| RegistryNNumber | registry_n_number_raw | source_payload 및 raw_record | 팀의 tail_number와 자동 동일시하지 않음 |
| AircraftSerialNumber | aircraft_serial_raw | source_payload 및 raw_record | 앞자리 0을 포함한 문자열 유지 |
| HowDiscoveredCode | how_discovered_code/description | source_payload 및 raw_record | 공식 보조 코드표 설명이 있는 값만 사용 |
| StageOfOperationCode | stage_of_operation_code/description | source_payload 및 raw_record | 원본 코드·설명 보존 |
| PartMake/Name/Number/Condition/Location | part 객체 | part jsonb | R02 component와 별개. 부품 정보를 구성품 값으로 몰래 대체하지 않음 |
| ComponentMake/Name/Model/PartNumber/Location | component 객체 | component jsonb | R02 component는 string 또는 null이므로 아래 투영 규칙 적용 |
| Discrepancy | narrative_raw | narrative_raw text | narrative_excerpt는 검색 위치 주변의 실제 원문 발췌. LLM 요약 아님 |
| 위 목록 외 엔진·시간·사이클·구조·조건·예방 코드 등 | 정제에 일부 없음 | **raw_record의 원본 76컬럼** | 조회에 필요해질 때 별도 컬럼 승격. 현재 버리지 않음 |

### 정제 과정에서 추가한 값

| 정제 필드 | 보존·처리 |
|---|---|
| model_family/model_granularity/model_catalog_match/model_certification_verified | source_payload. exact_label과 인증 완료를 구분 |
| symptom_candidate, segmentation_method | source_payload. 규칙 기반 후보로 보존, 실제 증상 정답으로 표시하지 않음 |
| reported_action_candidates, reported_outcome_candidates | 전용 jsonb 컬럼과 source_payload. text/start/end 구조 보존 |
| search_eligible, split | 테이블에 승격하고 true/corpus 조건 검사 |
| quality_flags, provenance | source_payload, 필요 시 warnings로 투영 |
| source_type/source_id/source_version | SDR 타입·case_id와 source_version. 타입·ID 불일치는 적재 실패 |
| evidence_id/evidence_group_id/split_group_id | evidence_id는 evidence 레지스트리, 그룹 둘은 SDR 컬럼 |
| locator, quote | evidence.locator/quote; raw narrative와 오프셋 대조 |
| source_file/source_record_no | SDR 컬럼. source_record_no는 CSV 헤더 제외 1부터 시작하는 논리 레코드 번호, 물리 줄 번호 아님 |

R02 출처 위치 필드는 팀 Google 명세의 `source_locator`를 사용하며 로컬 JSON도 이에 맞춰 수정했습니다. 변경 전 패키지 원본은 `locator`이므로 그대로 응답 스키마에 사용하지 않습니다. R05의 위치 필드는 `locator`입니다.

R02 `reported_actions`는 후보 객체의 text 배열만 투영하고 좌표·추출 방식은 저장소에 보존합니다. `cited_references`는 같은 release와 사례의 정규화 번호 목록을 중복 제거해 반환합니다. 여러 출현 레코드는 R03에서 별도로 유지합니다.

R02 `component`는 **제안**으로 component.name/make/model/part_number/location의 비어 있지 않은 값을 필드명이 붙은 문자열로 조합합니다. 전부 없으면 null입니다. PartName으로 대체하거나 원문에 없는 명칭을 생성하지 않습니다. 이 표현은 명세의 string/null 타입을 지키는 결정적 투영이며 나현님과 검토합니다.

## AMM 참조 → DB → R03

| 정제 입력 | DB/처리 | MCP |
|---|---|---|
| distilled_record_id | AMM 출현 업무 키. 번호를 키로 사용하지 않음 | 동일 값 |
| source_case_id | 같은 release의 sdr_cases.case_id로 찾아 sdr_case_uuid FK | 원본 case_id 반환 |
| operator_code/model_key/reported_jasc/difficulty_date/evidence_group_id/split_group_id/split | parent SDR와 일치 검사. 관계형 조회에서는 parent JOIN, 원본 파생 값은 source_payload 보존 | 부모 범위·그룹 반환 |
| cited_amm_ref_raw/normalized | 두 text 컬럼 | 원문·정규화값 함께 반환 |
| reference_level | text CHECK | chapter_section/subsection/task_format; Task 존재 검증 아님 |
| reference_start/end | integer | raw narrative의 Unicode 문자 기준 0-based, end-exclusive. UTF-8 바이트/UTF-16 위치 아님 |
| quote/quote_start/quote_end | text/integer, evidence에도 동일 위치 연결 | 인용문 반환, R05로 대조 |
| reported_action/action_start/action_end | nullable text/int. 문구는 원문 범위의 strip() 결과, 좌표는 공백 포함 원문 기준 | 조치가 없으면 null. 성공으로 추정하지 않음 |
| symptom/outcome | nullable text | 후보·미상 유지 |
| relationship_status | text CHECK | explicit/ambiguous/unlinked, 전문가 검증 의미 아님 |
| extraction_status/semantic_review_status/applicability_status/extraction_version | 전용 컬럼 | 명세 필드와 공통 warnings로 상태 전달. 현재 not_reviewed/not_verified |
| symptom_relationship_status/provenance | source_payload | 사례 맥락과 특정 참조의 인과를 구분 |
| source_type/source_id/source_version | 타입·업무 키 검증, source_version 컬럼 | derived_amm_reference, 출처·추출 버전 |
| evidence_id/locator/parent_source_ids | evidence로 투영, parent_source_ids와 실제 FK 일치 검사 | 근거 ID, 부모 원본 ID |
| search_eligible | true 검사, source_payload | 미적격 자료 검색 제외 |

번호 정규화는 원본을 보존하고 기존 정제값과 검증된 규칙만 사용합니다. 기종·운항사·원본을 제거한 번호 단독 병합은 하지 않습니다. 같은 번호라도 여러 출현·여러 맥락을 유지합니다.

조치 문구는 패키지 scripts/build_dataset.py의 추출 규칙에 따라 앞뒤 공백이 제거되지만 action_start/end는 원래 범위입니다. 19,621건이 공백 차이만 있고 36건은 정확히 일치하며 15,739건은 null입니다. 조치 검증에만 strip()을 적용하고 quote·참조번호·청크의 정확 원문 일치 검사는 유지합니다.

## 청크·벡터

| 입력 | 설계 |
|---|---|
| chunk_id, source_case_id, source_version | 청크 업무 키, 부모 FK, 원본 버전 |
| text, context_symptom, start_offset/end_offset | 청크 컬럼. text는 narrative 원문 범위와 일치해야 함 |
| embedding_text/content_hash | 실제 임베딩 입력과 UTF-8 SHA-256. text의 해시와 혼동하지 않음 |
| linked_reference_ids | amm_chunk_references 연결 행으로 변환. 같은 release·같은 SDR만 허용 |
| reference_numbers/reference_quality/relationship_statuses | source_payload 보존, 부모 참조와 검산. 별도 정답 매핑 아님 |
| scope/group/date/JASC | 부모 SDR JOIN, 입력 메타데이터 일치 검사 |
| embedding_status/model_version/embedding | 원본은 모두 not_generated/null/null. 실제 생성 전 READY 표시 금지 |
| semantic_review_status/applicability_status | source_payload, 원본 한계 유지 |

벡터는 청크당 임베딩 입력을 기준으로 생성합니다. 번호마다 같은 본문을 반복 임베딩하지 않습니다. 내용 해시·모델·차원·버전이 같으면 재사용 가능하도록 설계하되 저장소는 pgvector의 rag 스키마로 확정했습니다. 모델·차원·문서/질의 전처리는 임베딩 생성 전에 정합니다. 청크 점수를 여러 참조에 연결할 때 같은 참조의 중복 결과를 정리하고 독립 증거 수는 evidence_group_id로 판단합니다.

## JASC → DB → R01

| 입력 | 보존·출력 |
|---|---|
| code | 문자열 4자리. release 안에서 유일. 정수 변환 금지 |
| title, title_status | 원문 그대로. 2924는 code_only_no_title_found이며 공식 명칭을 추정하지 않음 |
| definition | nullable text. 61개는 null |
| source_url, definition_source_url/file, title_source_file | 각각 보존. 제목 출처와 정의 출처를 섞지 않음 |
| codebook_version | 행별 원본 버전 문자열 그대로 |
| provenance/reference_coverage | source_payload. 두 공식 자료를 합친 패키지 범위임을 보존 |

활성 코드표 전체를 선택하는 snapshot은 release에 연결합니다. 행별 codebook_version은 487개와 61개가 서로 다르므로 단일 문자열로 덮어쓰지 않습니다. R01의 필수 반환 필드는 원문 값으로 채우며 2924 명칭 미확인은 공통 warnings로 전달하는 안입니다. JASC 자체는 항공사·모델별로 복제하거나 해당 SDR에 등장하는 코드만으로 제한하지 않습니다.

## 근거 → R05

R02/R03에서 발급된 근거를 동일 접수 권한·범위·스냅샷으로 확인합니다. 레지스트리는 원본 JSONL의 evidence_id, locator, quote와 연결을 사용합니다. 새로운 데이터 버전에서도 동일 evidence_id가 재등장할 수 있어 `(release_id, evidence_id)`로 찾습니다.

- SDR: source_id=case_id, source_version=원본 파일 리비전, parent_source_ids=[]; text는 보존 원문.
- AMM: source_id=distilled_record_id, source_version=원본 리비전+추출 버전, parent_source_ids=[source_case_id]; text는 해당 SDR의 인용 범위 원문.
- quote_spans는 원본 narrative 기준 절대 문자 좌표입니다. AMM text가 부분 범위면 locator에 그 시작/끝을 명시하고 0으로 재설정하지 않습니다.
- 원본 locator의 추가 record_index_definition은 저장소에 보존하되 엄격한 outputSchema 밖으로 그대로 내보내지 않습니다.
- text/quote 길이가 계약 상한을 넘는 미래 자료는 명시된 범위로 분할해야 합니다. 원문을 잘라 놓고 전체 원문인 것처럼 표시하지 않습니다.
- 권한/범위가 다른 근거, 오래된 스냅샷은 내용이나 존재를 누설하지 않고 계약에 맞게 거부합니다.

## 항공사·모델 매핑

[운항사 목록](mappings/operator-inventory.csv), [모델 목록](mappings/model-inventory.csv), [범위별 건수](mappings/scope-coverage.csv)는 **관측 목록**입니다. 서비스 대응 필드는 비워 두고 UNRESOLVED로 표시했습니다. 자동 하이픈 제거, 운항사 접미어 제거, 문자열 유사도로 VERIFIED를 만들지 않습니다.

| 관계 | 현재 확인 | 필요한 승인 정보 |
|---|---|---|
| 팀 ICAO airline_code → SDR operator_code | 검색용 24개 코드 관측. 검증된 대응표 없음 | 코드 양쪽, 공식/팀 마스터 근거, 유효기간, 검토자, mapping_revision |
| 팀 aircraft_model → SDR model_key | 38개 상세 표기 관측. A320-214는 PDF 예시, A320214는 데이터에 존재 | 정확 모델의 동등성 근거, 별칭, 검토자·버전. 계열 전체 매핑 금지 |
| 서비스 tenant → 항공사 접근 권한 | PDF는 사용자 소속 항공사, Google은 tenant 권한도 명시 | Backend의 실제 권한 모델. ICAO를 tenant_id로 가정하지 않음 |
| JASC → ATA/부서/AMM | 자동 대응 근거 없음 | 별도 검토된 힌트가 필요. 일대일 정답표를 만들지 않음 |

승인 매핑은 Backend 소유 파일/테이블에 별도 버전으로 관리하는 제안입니다. 최소 필드: mapping_id(UUID), source_system, source_code, target_system, target_code, valid_from/to(nullable), status, evidence_locator, reviewed_by, mapping_revision. VERIFIED만 사용하고 모호한 다중 대상은 미확정으로 처리합니다. 생성 목록 재실행이 승인 데이터를 덮어쓰지 않게 분리합니다. 아직 해당 테이블 위치·관리 API는 미정입니다.

## 이번 점검의 범위

`scripts/profile_source_data.py`가 원본·정제 필드 통계, 원문/버전 연결, 인용 위치, 청크 해시, 같은 사례 참조 연결, 코드 존재, 검색/평가 그룹 분리, SQLite 건수, manifest 50개 파일 해시를 검사합니다. [보고서](../reports/design-data-profile.json)의 실제 검사 목록이 기준입니다. 코드 의미의 외부 검증, 모델 인증, PostgreSQL 실행, 검색 정확도는 검사하지 않았습니다.
