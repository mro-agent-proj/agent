# PostgreSQL·pgvector 구현

기준일 2026-10-21. 실행 가능한 컬럼·제약의 기준은 [SQLAlchemy metadata](../src/mro_rag/db.py), 배포 이력은 [고정 Alembic 마이그레이션](../migrations/versions/0001_rag.py)과 동일 내용의 [Backend Flyway SQL](../integration/backend/V3__rag_evidence.sql)입니다. 공유 DB에서는 Flyway만 실행합니다. [ERD](erd.mmd)는 관계를 요약합니다.

## 테이블

| 위치 | 역할 | 최초 전체 적재 |
|---|---|---:|
| public.knowledge_releases | 전체 manifest 해시+변환 버전, READY 상태 | 1 |
| public.jasc_codes | 4자리 코드·명칭·정의·출처 | 548 |
| public.sdr_cases | 검색 corpus, 원본 76필드와 정제 payload | 71,741 |
| public.amm_references | SDR의 개별 AMM 참조 출현·인용/조치 위치 | 35,396 |
| public.amm_chunks | 원문 범위·embedding_text·해시 | 31,678 |
| public.amm_chunk_references | 같은 SDR의 청크↔참조 연결 | 35,396 |
| public.evidence | EV-SDR/EV-AMM 외부 근거 키·인용 위치 | 107,137 |
| public.knowledge_indexes | 검색 방식별 설정 해시·모델·준비 상태 | 4 |
| public.rag_request_contexts | Backend가 발급한 요청별 권한·검색 스냅샷 | 실행별 |
| rag.amm_embeddings | 청크별 vector(384) | 31,678 |

`users`, `aircraft`, `maintenance_history`, `repair_requests` 등 업무 테이블은 생성·적재하지 않습니다. Backend V1/V2와 위 구조의 공존을 별도 DB에서 검증했습니다. `langgraph` 체크포인트도 범위 밖입니다.

## 보존과 무결성

내부 PK는 UUID v4이며 원본 case_id/distilled_record_id/chunk_id/evidence_id는 그대로 보존합니다. 같은 manifest+변환 버전 재실행은 기존 release를 재사용합니다. 원본·추출 규칙이 바뀌면 새 release를 생성합니다. 원본 SDR snapshot만으로 전체 패키지의 동일성을 판단하지 않습니다.

파생 테이블의 복합 FK는 같은 release를 강제합니다. 청크↔AMM 연결과 AMM 근거 FK는 같은 원본 SDR까지 확인합니다. AMM 번호는 여러 자료에 반복되므로 UNIQUE로 두지 않습니다. 검색에는 corpus만 적재하며 평가 holdout은 제외합니다.

SDR raw_record에 원본 76개 필드, source_payload에 정제 입력을 보존합니다. submission_date는 원본 달력 날짜, submission_at은 UTC 시점, submission_date_raw는 원본 문자열입니다. narrative_raw는 문자 오프셋 기준이므로 정규화하지 않습니다.

AMM 번호·quote·청크의 원문 substring 일치를 검사합니다. 원본 추출기의 reported_action만 `.strip()` 처리된 값이므로 `narrative[action_start:action_end].strip()`으로 검증하고 오프셋과 원본 조치는 바꾸지 않습니다. 상세 관측값은 [데이터 매핑](DATA_MAPPING.md)을 참고합니다.

JASC는 문자열 4자리로 보존하고 ATA·AMM 번호·부서로 자동 변환하지 않습니다. 정의 없는 61개와 명칭 미확인 2924를 그대로 유지합니다. 실제 코드북에 없는 코드는 실패시킵니다.

## 검색 구조

- JASC: 코드 정확 조회 또는 명칭/정의 영어 전문검색.
- SDR: PostgreSQL `english` tsvector와 GIN. operator/model/release와 고정 필터를 SQL에서 적용합니다. 영어 원문 lexical 검색이며 한국어 의미 검색은 R03이 담당합니다.
- AMM 번호: 정규화 번호의 관계형 정확 조회. query와 함께 오면 번호 일치 자료 내 벡터 순위.
- AMM 벡터: `intfloat/multilingual-e5-small`, revision `614241f622f53c4eeff9890bdc4f31cfecc418b3`, 384차원, cosine, 정규화. query:/passage: 접두사. 480토큰 창/448토큰 간격으로 전체 입력을 처리하고 창 벡터 평균 후 정규화합니다.
- pgvector 0.8.2/PG16에서 범위 안 정확 최근접 검색을 사용합니다. HNSW는 아직 만들지 않습니다. 동일 embedding_text 해시는 계산만 재사용하고 청크·근거 ID를 합치지 않습니다.

모델의 기본 제한과 접두사는 [모델 카드](https://huggingface.co/intfloat/multilingual-e5-small)를 따르며, 창 평균은 이 구현의 전처리 정책입니다. 모든 실제 입력의 창이 모델 상한 이내인지 검증했습니다. 이는 의미 검색 품질 평가를 대신하지 않습니다. 점수는 확률이 아니며 후보 JASC는 순위 힌트, locked_filters의 JASC는 필터입니다.

## 실행 컨텍스트와 읽기 전용

Backend가 `rag_request_contexts`에 request/run/subject/tenant/context_version, operator/model, release_id, index_key 배열, mapping_revision, locked_filters, 만료·활성 여부를 게시합니다. [내부 계약](../integration/backend/README.md)에 연결 규칙이 있습니다. `publish_context`는 단조 증가 버전과 불변 스냅샷을 강제하는 참조 구현입니다.

서버는 매 호출 위임 JWT와 DB 컨텍스트를 대조하고 READ ONLY/REPEATABLE READ 트랜잭션에서 조회합니다. 다른 접수·사용자·버전·철회된 실행은 거부합니다. 미확정 operator/model은 NEEDS_INPUT이며 전체 범위로 확대하지 않습니다. R01 코드표만 항공사·모델 필터를 사용하지 않습니다.

색인은 자료 종류/방식별 READY 레코드로 관리하며 run이 고정한 색인만 조회합니다. 기존 run 중간에 새 모델을 적용하지 않습니다. 미준비/사라진 색인은 명시적 오류이고 lexical로 조용히 대체하지 않습니다. cursor는 권한·질의·범위·버전·한도에 서명으로 묶고 만료를 검사합니다.

## 적재·운영

DDL 생성 → manifest/원본 검증 → release 및 JASC/SDR/AMM/청크/연결/근거의 단일 트랜잭션 적재 → 관계형 READY → 별도 전체 임베딩 트랜잭션 → vector READY 순서입니다. 실패하면 일부 자료를 공개하지 않습니다. 오프라인 운영 계정만 쓰고 MCP 계정은 열 개 테이블 SELECT만 허용합니다.

FK는 기본 삭제 제한을 사용합니다. 실행 중 release/색인을 삭제하거나 기존 원본을 덮어쓰는 관리 API는 제공하지 않습니다. 자동 downgrade는 지원하지 않습니다. 재현 절차는 [RUNBOOK](RUNBOOK.md), 실측은 [검증 보고서](../reports/implementation-review.md)입니다.
