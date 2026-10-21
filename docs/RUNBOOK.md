# 로컬 실행 안내

Python 3.12, uv, Docker Compose가 필요합니다. 아래 적재·마이그레이션 명령은 **격리된 로컬 제작·검증 DB(55432) 전용**입니다. 최신 팀 규약 v0.3에 따라 공유 DB(5432)의 생성·계정·전체 데이터 적재는 Backend가 소유합니다. 공유 DB에는 여기의 Alembic/ingest/embed를 실행하지 않습니다.

팀 연결은 [Backend 안내](../integration/backend/README.md)의 순서를 따릅니다. Backend Compose → Spring(AI_MODE=rabbit, Flyway/업무 시드) → Backend `scripts/rag-restore.sh` → MCP/AI 순서입니다. Spring 기동만으로 RAG 덤프가 자동 복원되지는 않습니다. 현재 JWT/컨텍스트 전달과 시연 검색 범위는 팀 미정(C01/C02)이므로 데이터 복원과 실제 접수 호출 성공을 구분합니다.

## 환경과 DB

```sh
uv sync --frozen --extra dev --extra embedding
cp .env.example .env
```

`.env`의 `RAG_JWT_SECRET`, `RAG_CURSOR_SECRET`을 서로 다른 32자 이상의 임의 값으로 바꿉니다. 예: `openssl rand -hex 32`를 각각 실행합니다. 예제 비밀번호는 loopback 개발 DB 전용입니다. 운영 인증정보는 배포 환경에서 주입합니다. Makefile/CLI는 `.env`를 자동 로딩하지 않으므로 다음을 실행합니다.

```sh
set -a
source .env
set +a
make db
make migrate
docker compose exec -T db psql -U mro_owner -d mro -v ON_ERROR_STOP=1 < scripts/create_reader.sql
```

서버에는 `RAG_DATABASE_URL`의 SELECT 전용 계정만 사용합니다. 시작 시 쓰기 권한·슈퍼유저 여부를 검사합니다. `DATABASE_URL`은 오프라인 적재·마이그레이션용입니다. 두 역할을 혼용하지 않습니다. DB 포트는 호스트 `127.0.0.1:55432`, MCP는 `127.0.0.1:8102`입니다.

## 원본 적재와 임베딩

원본 패키지를 `data/source/airbus-mcp-data-v1/`에 놓거나 `MRO_SOURCE_DIR`을 지정합니다. 필요한 폴더는 `runtime`, `reference`, `rag`, `canonical`, `evaluation`, `provenance`이고 최상위 `package_manifest.json`으로 모든 파일 해시를 확인합니다. 파일을 수정하면 적재가 거부됩니다. 기존 SQLite 파일은 실행하지 않습니다.

```sh
make seed
# 출력된 release_key 사용
make embed RELEASE=REL-c3bab06428c93938342ef46b-postgres-v1
make serve
```

첫 임베딩 실행은 공개 모델 가중치를 다운로드합니다. 이후 캐시로 로컬 실행합니다. CPU가 기본이고 Apple Silicon은 `MRO_EMBED_DEVICE=mps`를 사용할 수 있습니다. 임베딩은 실행 요청과 분리된 운영 명령이며 데이터 전체가 완성되어야 READY가 됩니다. 같은 패키지·설정 재실행은 기존 결과를 재사용합니다. GPU 여부에 따라 최초 구축 시간이 달라집니다.

R03의 첫 질의는 모델 로딩 때문에 느릴 수 있습니다. 서비스 공개 전에 신뢰된 컨텍스트로 준비 질의를 실행하고, HTTP 클라이언트의 기본 10초 제한을 점검합니다. 데이터 임베딩은 질의 중 생성하지 않습니다.

## 인증된 도구 호출

`GET /health`는 DB 연결만 확인합니다. 데이터·색인 준비나 Backend 연결 완료를 뜻하지 않습니다. `/mcp`는 모든 요청에 `Authorization: Bearer <delegated JWT>`를 요구합니다.

현재 구현은 Backend가 접수 접근권한과 범위를 확인하고 `rag_request_contexts`에 고정 컨텍스트를 작성한 뒤 토큰을 발급하는 방식입니다. 최신 PDF에서 이 전달 방식은 미정이므로 [통합 안내](../integration/backend/README.md)의 C01을 먼저 맞춰야 합니다. 개발 테스트에서는 `tests/conftest.py`의 `make_context`와 `tests/test_http.py`의 토큰/SDK 예시를 사용합니다. LLM 도구 입력에 토큰이나 operator 값을 넣지 않습니다.

```json
{"request_id":"<authorized request UUID>","context_version":1,"codes":["3230","3242"]}
```

이는 R01 입력 예시입니다. R02는 `query`, R03은 `query` 또는 `amm_reference_exact`, R05는 이전 결과의 `evidence_id`를 사용합니다. 전체 스키마는 [도구 JSON](spec-v1/mcp-tool-contracts.json)에 있습니다. 서버는 네 도구만 등록합니다. 클라이언트는 `retryable=true`인 오류에만 최대 1회 재시도하고 잘못된 권한·버전·입력은 재시도하지 않습니다.

## 자동 검증

테스트는 별도 DB 이름이 `mro_test`로 시작할 때만 실행합니다. 임베딩 모델 없이도 SQL·권한·MCP 프로토콜을 검증할 수 있습니다.

```sh
docker compose exec -T db psql -U mro_owner -d postgres -c 'CREATE DATABASE mro_test'
export TEST_DATABASE_URL=postgresql+asyncpg://mro_owner:local-dev-only@127.0.0.1:55432/mro_test
export TEST_RAG_DATABASE_URL=postgresql+asyncpg://mro_reader:local-reader-only@127.0.0.1:55432/mro_test
DATABASE_URL="$TEST_DATABASE_URL" uv run alembic upgrade head
docker compose exec -T db psql -U mro_owner -d mro_test -v ON_ERROR_STOP=1 < scripts/create_reader.sql
make test
make lint
```

`CREATE DATABASE`는 최초 한 번만 실행합니다. pytest는 각 실행 컨텍스트를 고유 ID로 생성하고 동일 합성 데이터 release를 재사용합니다. `.github/workflows/test.yml`은 빈 DB에서 동일 과정을 수행합니다.

전체 원본 검사: `uv run python scripts/profile_source_data.py`.
전체 적재 DB 검사: `uv run --extra embedding python scripts/verify_full_corpus.py`.
후자는 단일 READY release가 있는 로컬 검증 DB용이며 임시 컨텍스트 작성 후 비활성화합니다. 한국어/영어 벡터 검색과 R05 원문 일치·모든 입력의 모델 토큰 상한을 검사하지만 검색 품질 평가를 대신하지 않습니다.

## 버전 변경

원본·변환 규칙·모델·전처리가 바뀌면 새 release/색인을 구축합니다. 기존 run은 이전 버전을 계속 참조합니다. 같은 자료 종류·검색 방식의 READY 색인이 여럿이면 Backend가 `index_keys`로 하나씩 명시해야 합니다. 사용 중인 release/색인을 삭제하지 않습니다. 이 초기 마이그레이션의 자동 downgrade는 데이터 삭제 방지를 위해 지원하지 않습니다.
