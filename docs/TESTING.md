# DB 연결 테스트

기본 `pytest`는 DB 없이 도는 테스트만 실행하고 나머지는 skip합니다. 아래는 DB 테스트까지 돌리는 방법입니다.

DB 테스트까지 돌리려면 CI(`.github/workflows/test.yml`)처럼 빈 pgvector DB를 준비합니다.

```bash
export TEST_DATABASE_URL=postgresql+asyncpg://mro_owner:local-dev-only@127.0.0.1:55432/mro_test
export TEST_RAG_DATABASE_URL=postgresql+asyncpg://mro_reader:local-reader-only@127.0.0.1:55432/mro_test
DATABASE_URL="$TEST_DATABASE_URL" uv run alembic upgrade head
psql ... -f scripts/create_reader.sql
uv run --extra dev pytest                     # RAG DB 테스트; business DB 테스트는 아래 별도 설정
```

테스트는 독립 합성 데이터를 만들어 실행하므로 원본 파일이 없어도 재현됩니다.

CI의 `business-db` 작업은 RAG용 Alembic DB와 분리된 `mro_test_business` DB에
**Backend main의 원본 Flyway 마이그레이션**을 적용합니다. Backend SHA를 로그에 기록하고,
`tests/fixtures/business.sql`로 가상 기체·부서·요청·실행·과거/미래 이력을 넣습니다.
실제 business-mcp 구현은 `mro_business_reader`로 접속하여 B01 범위, B02 동일 기체·접수 이전
이력 및 페이지, B03 담당 부서를 확인합니다. 계정의 쓰기 권한과 users 조회 권한도 검사합니다.

로컬 재현은 `.github/workflows/test.yml`의 `business-db` 순서를 따릅니다.
`BUSINESS_TEST_DATABASE_URL`, `BUSINESS_TEST_REQUEST_ID`, `BUSINESS_TEST_RUN_ID`를 해당 fixture와
맞추고 `REQUIRE_BUSINESS_DB_TESTS=1 uv run pytest tests/test_business.py -q -rs`를 실행합니다.
필수 모드에서는 환경변수 누락이 skip 대신 실패가 됩니다. fixture는 공유 DB에 적용하지 않습니다.
이 테스트는 실제 MCP 구현과 DB의 연결 검사이며, 공개 HTTP 경로의 종단간 연결이나 실제 LLM
정확도 평가와는 별개입니다.
