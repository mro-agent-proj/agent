#!/usr/bin/env python3
"""OpenAI 임베딩(text-embedding-3-large, 1024차원)을 만들고 Backend 전달본으로 묶습니다.

  # SDR 원문 (R02 하이브리드, Backend V7) / 기술자료 조각 (R04, Backend V8). 비어 있는 행만, 재실행하면 이어서 함
  OPENAI_API_KEY=... uv run python scripts/embed_openai.py embed
  OPENAI_API_KEY=... uv run python scripts/embed_openai.py embed-tech
  # 전달본 (data-only 덤프 + manifest + verify.sql + SHA256SUMS + Flyway 사본)
  uv run python scripts/embed_openai.py package        # sdr-embeddings_v1
  uv run python scripts/embed_openai.py package-tech   # airbus-safety-first_v2 (문서·조각·OpenAI 임베딩)
  uv run python scripts/embed_openai.py package-rag    # sdr-jasc-amm_v2 (V8에서 AMM 의미 검색 3개 테이블을 뺀 RAG 6개 테이블)

임베딩 입력은 narrative_raw 하나뿐입니다(JASC 등 정답에 가까운 필드는 넣지 않음). content_hash는 원문 sha256이라
원문이 바뀐 행은 verify.sql의 stale_hash로 드러납니다. 비용: SDR 71,741건 약 450만 토큰, 약 $0.6 (2026-10 단가).
"""

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import asyncpg
import httpx

MODEL, DIMENSIONS = "text-embedding-3-large", 1024
MODEL_VERSION = f"openai:{MODEL}@{DIMENSIONS}"
BATCH, CONCURRENCY, MAX_CHARS = 256, 4, 20000

VERIFY_SQL = """select 'sdr_embeddings', count(*)::text,
       md5(coalesce(string_agg(sdr_case_uuid::text || ':' || content_hash || ':' || embedding_model_version,
                               ',' order by sdr_case_uuid), ''))
from rag.sdr_embeddings
union all
select 'stale_hash', count(*)::text, ''
from rag.sdr_embeddings e join public.sdr_cases s on s.release_id = e.release_id and s.id = e.sdr_case_uuid
where e.content_hash <> encode(sha256(convert_to(s.narrative_raw, 'UTF8')), 'hex');
"""


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


async def embed_batch(client, key, rows, attempt=0):
    body = {"model": MODEL, "dimensions": DIMENSIONS, "input": [r["narrative_raw"][:MAX_CHARS] or " " for r in rows]}
    resp = await client.post("https://api.openai.com/v1/embeddings", json=body, headers={"Authorization": f"Bearer {key}"})
    if resp.status_code in (429, 500, 502, 503) and attempt < 6:
        await asyncio.sleep(2**attempt)
        return await embed_batch(client, key, rows, attempt + 1)
    resp.raise_for_status()
    return [d["embedding"] for d in sorted(resp.json()["data"], key=lambda d: d["index"])]


async def embed(dsn, key, limit=None):
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=CONCURRENCY + 1)
    todo = await pool.fetch(
        """SELECT s.release_id, s.id, s.narrative_raw FROM public.sdr_cases s
           LEFT JOIN rag.sdr_embeddings e ON e.release_id = s.release_id AND e.sdr_case_uuid = s.id
           WHERE e.sdr_case_uuid IS NULL ORDER BY s.id""" + (f" LIMIT {int(limit)}" if limit else "")
    )
    print(f"임베딩할 SDR {len(todo)}건", flush=True)
    sem, done = asyncio.Semaphore(CONCURRENCY), 0
    async with httpx.AsyncClient(timeout=120) as client:
        async def work(rows):
            nonlocal done
            async with sem:
                vectors = await embed_batch(client, key, rows)
                await pool.executemany(
                    """INSERT INTO rag.sdr_embeddings (release_id, sdr_case_uuid, content_hash, embedding_model_version, embedding)
                       VALUES ($1, $2, $3, $4, $5::vector) ON CONFLICT DO NOTHING""",
                    [(r["release_id"], r["id"], sha(r["narrative_raw"]), MODEL_VERSION, json.dumps(v)) for r, v in zip(rows, vectors, strict=True)],
                )
                done += len(rows)
                if done % (BATCH * 20) < BATCH:
                    print(f"  {done}/{len(todo)}", flush=True)
        await asyncio.gather(*(work(todo[i:i + BATCH]) for i in range(0, len(todo), BATCH)))
    print(f"완료: {done}건", flush=True)
    await pool.close()


async def package(dsn, container, migration, out):
    out.mkdir(parents=True, exist_ok=True)
    conn = await asyncpg.connect(dsn)
    rows = {r[0]: (r[1], r[2]) for r in await conn.fetch(VERIFY_SQL)}
    total = int(rows["sdr_embeddings"][0])
    sdr = await conn.fetchval("SELECT count(*) FROM public.sdr_cases")
    release = await conn.fetchval("SELECT string_agg(release_key, ',') FROM public.knowledge_releases")
    await conn.close()
    if total != sdr or rows["stale_hash"][0] != "0":
        sys.exit(f"아직 덜 만들어졌습니다: 임베딩 {total} / SDR {sdr}, stale {rows['stale_hash'][0]}")
    dump = out / f"sdr-embeddings-v1-{date.today():%Y%m%d}.dump"
    with open(dump, "wb") as f:
        subprocess.run(["docker", "exec", container, "pg_dump", "-U", "mro", "-d", "mro", "--data-only", "--format=custom",
                        "--compress=6", "--no-owner", "--no-acl", "-t", "rag.sdr_embeddings"], stdout=f, check=True)
    (out / "verify.sql").write_text(VERIFY_SQL)
    lines = Path(migration).read_text().splitlines(keepends=True)
    (out / Path(migration).name).write_text("".join(lines[2:]))  # Flyway 파일에서 출처 주석 2줄 제외 (check_schema 규칙)
    manifest = {
        "package": out.name, "built_on": date.today().isoformat(), "release_key": release,
        "embedding": {"provider": "openai", "model": MODEL, "dimensions": DIMENSIONS, "version": MODEL_VERSION,
                      "input": "sdr_cases.narrative_raw (앞 20,000자)", "distance": "cosine"},
        "counts": {k: v[0] for k, v in rows.items()},
        "content_md5": {k: v[1] for k, v in rows.items()},
        "dump": {"file": dump.name, "tables": ["rag.sdr_embeddings"], "format": "pg_dump custom, --data-only"},
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    sums = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in sorted(out.iterdir()) if p.name != "SHA256SUMS"]
    (out / "SHA256SUMS").write_text("".join(sums))
    print(json.dumps(manifest["counts"]), dump.stat().st_size // 1_000_000, "MB")


TECH_VERIFY_SQL = """-- 복원 확인: 건수, 원문 위치 일치, 테이블별 내용 해시 (임베딩은 OpenAI 1024차원)
SELECT 'tech_documents' AS t, count(*)::text AS n, md5(string_agg(document_id || pdf_sha256 || md5(body_text), ',' ORDER BY document_id)) AS h FROM public.tech_documents
UNION ALL
SELECT 'tech_chunks', count(*)::text, md5(string_agg(evidence_id || content_hash || start_offset || ':' || end_offset, ',' ORDER BY chunk_id)) FROM public.tech_chunks
UNION ALL
SELECT 'tech_embeddings', count(*)::text, md5(string_agg(chunk_id || content_hash || embedding_model_version, ',' ORDER BY chunk_id)) FROM rag.tech_embeddings
UNION ALL
SELECT 'offset_mismatch', count(*)::text, '' FROM public.tech_chunks c JOIN public.tech_documents d USING (document_id)
 WHERE substr(d.body_text, c.start_offset + 1, c.end_offset - c.start_offset) <> c.text;
"""

RAG_TABLES = [("public", "amm_references"), ("public", "evidence"), ("public", "jasc_codes"),
              ("public", "knowledge_indexes"), ("public", "knowledge_releases"), ("public", "sdr_cases")]
ROW_HASH = ("SELECT count(*), encode(sha256(convert_to(COALESCE(string_agg(row_hash, '' ORDER BY row_hash), ''), 'UTF8')), 'hex') "
            "FROM (SELECT encode(sha256(convert_to(to_jsonb(t)::text, 'UTF8')), 'hex') AS row_hash FROM {schema}.{table} t) hashes")


async def embed_tech(dsn, key):
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    todo = await pool.fetch("""SELECT c.chunk_id, c.embedding_text AS narrative_raw FROM public.tech_chunks c
        LEFT JOIN rag.tech_embeddings e USING (chunk_id) WHERE e.chunk_id IS NULL ORDER BY c.chunk_id""")
    print(f"임베딩할 기술자료 조각 {len(todo)}건", flush=True)
    async with httpx.AsyncClient(timeout=120) as client:
        for i in range(0, len(todo), BATCH):
            rows = todo[i:i + BATCH]
            vectors = await embed_batch(client, key, rows)
            await pool.executemany(
                """INSERT INTO rag.tech_embeddings (chunk_id, content_hash, embedding_model_version, embedding)
                   VALUES ($1, $2, $3, $4::vector) ON CONFLICT DO NOTHING""",
                [(r["chunk_id"], sha(r["narrative_raw"]), MODEL_VERSION, json.dumps(v)) for r, v in zip(rows, vectors, strict=True)],
            )
    print(f"완료: {len(todo)}건", flush=True)
    await pool.close()


def write_package(out, container, tables, migrations, manifest, verify_sql):
    """data-only dump of the tables + verify.sql + Flyway copies (source comment lines removed) + SHA256SUMS."""
    out.mkdir(parents=True, exist_ok=True)
    dump = out / f"{out.name.replace('_', '-')}-{date.today():%Y%m%d}.dump"
    args = ["docker", "exec", container, "pg_dump", "-U", "mro", "-d", "mro", "--data-only", "--format=custom",
            "--compress=6", "--no-owner", "--no-acl"]
    for table in tables:
        args += ["-t", table]
    with open(dump, "wb") as f:
        subprocess.run(args, stdout=f, check=True)
    (out / "verify.sql").write_text(verify_sql)
    for migration, skip in migrations:
        lines = Path(migration).read_text().splitlines(keepends=True)
        (out / Path(migration).name).write_text("".join(lines[skip:]))
    manifest["dump"] = {"file": dump.name, "tables": tables, "format": "pg_dump custom, --data-only"}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    sums = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in sorted(out.iterdir()) if p.name != "SHA256SUMS"]
    (out / "SHA256SUMS").write_text("".join(sums))
    print(out, dump.stat().st_size // 1_000_000, "MB")


async def package_tech(dsn, container, migrations_dir, out):
    conn = await asyncpg.connect(dsn)
    rows = {r[0]: (r[1], r[2]) for r in await conn.fetch(TECH_VERIFY_SQL)}
    await conn.close()
    if rows["tech_embeddings"][0] != rows["tech_chunks"][0] or rows["offset_mismatch"][0] != "0":
        sys.exit(f"기술자료 임베딩이 덜 만들어졌습니다: {rows}")
    manifest = {
        "package": out.name, "built_on": date.today().isoformat(),
        "source": "Airbus Safety First 아카이브 PDF 61편 (2005-2013), https://safetyfirst.airbus.com/archive/ (문서·조각은 경님 tech-v1 전달본 그대로)",
        "embedding": {"provider": "openai", "model": MODEL, "dimensions": DIMENSIONS, "version": MODEL_VERSION,
                      "input": "tech_chunks.embedding_text", "distance": "cosine"},
        "counts": {k: v[0] for k, v in rows.items()},
        "content_md5": {k: v[1] for k, v in rows.items()},
    }
    write_package(out, container, ["public.tech_documents", "public.tech_chunks", "rag.tech_embeddings"],
                  [(migrations_dir / "V5__rag_tech_docs.sql", 2), (migrations_dir / "V8__drop_amm_semantic_openai_tech.sql", 2)],
                  manifest, TECH_VERIFY_SQL)


async def package_rag(dsn, container, migrations_dir, out):
    conn = await asyncpg.connect(dsn)
    expected = []
    for schema, table in RAG_TABLES:
        count, digest = await conn.fetchrow(ROW_HASH.format(schema=schema, table=table))
        expected.append((schema, table, count, digest))
    release = await conn.fetchval("SELECT string_agg(release_key, ',') FROM public.knowledge_releases")
    ready = await conn.fetchval("SELECT count(*) FROM public.knowledge_indexes WHERE state = 'READY'")
    await conn.close()
    values = ",\n        ".join(f"('{s}', '{t}', {n}::bigint, '{h}')" for s, t, n, h in expected)
    verify = f"""-- Read-only verification for the RAG v2 snapshot (V8: AMM semantic-search tables removed).
BEGIN READ ONLY;
SET LOCAL TIME ZONE 'UTC';
DO $verify$
DECLARE
    expected record;
    actual_count bigint;
    actual_hash text;
BEGIN
    FOR expected IN SELECT * FROM (VALUES
        {values}
    ) AS v(schema_name, table_name, row_count, content_hash)
    LOOP
        EXECUTE format($query$SELECT count(*), encode(sha256(convert_to(COALESCE(string_agg(row_hash, '' ORDER BY row_hash), ''), 'UTF8')), 'hex') FROM (SELECT encode(sha256(convert_to(to_jsonb(t)::text, 'UTF8')), 'hex') AS row_hash FROM %I.%I t) hashes$query$, expected.schema_name, expected.table_name)
            INTO actual_count, actual_hash;
        IF actual_count <> expected.row_count OR actual_hash IS DISTINCT FROM expected.content_hash THEN
            RAISE EXCEPTION 'RAG verification failed for %.%: expected % rows / %, got % rows / %',
                expected.schema_name, expected.table_name, expected.row_count, expected.content_hash,
                actual_count, actual_hash;
        END IF;
    END LOOP;
    IF (SELECT count(*) FROM public.knowledge_releases WHERE state = 'READY') <> 1
       OR (SELECT count(*) FROM public.knowledge_indexes WHERE state = 'READY') <> {ready} THEN
        RAISE EXCEPTION 'Expected one READY release and {ready} READY indexes';
    END IF;
END;
$verify$;
COMMIT;
SELECT 'PASS: all {len(RAG_TABLES)} RAG data tables match the supplied snapshot' AS verification;
"""
    manifest = {
        "package": out.name, "built_on": date.today().isoformat(), "release_key": release,
        "derived_from": "sdr-jasc-amm_v1 (세환님 rag-v1-20261021) minus amm_chunks, amm_chunk_references, rag.amm_embeddings and the derived_amm VECTOR index row (Backend V8)",
        "counts": {f"{s}.{t}": n for s, t, n, _ in expected},
    }
    write_package(out, container, [f"{s}.{t}" for s, t in RAG_TABLES],
                  [(migrations_dir / "V3__rag_evidence.sql", 2), (migrations_dir / "V8__drop_amm_semantic_openai_tech.sql", 2)],
                  manifest, verify)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["embed", "package", "embed-tech", "package-tech", "package-rag"])
    ap.add_argument("--dsn", default=os.environ.get("EMBED_DSN", "postgresql://mro:mro@127.0.0.1:5432/mro"))
    ap.add_argument("--limit", type=int, help="embed: 시험용 상한")
    ap.add_argument("--pg-container", default="backend-postgres-1")
    ap.add_argument("--migration", default="../backend/src/main/resources/db/migration/V7__rag_sdr_embeddings.sql")
    ap.add_argument("--out", type=Path, help="전달본 폴더 (기본: ../backend/data/rag-dump/<패키지>)")
    ap.add_argument("--migrations", type=Path, default=Path("../backend/src/main/resources/db/migration"))
    args = ap.parse_args()
    dump_root = Path("../backend/data/rag-dump")
    if args.command in ("embed", "embed-tech"):
        key = os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY")
        if not key:
            sys.exit("OPENAI_API_KEY가 필요합니다")
        asyncio.run(embed(args.dsn, key, args.limit) if args.command == "embed" else embed_tech(args.dsn, key))
    elif args.command == "package":
        asyncio.run(package(args.dsn, args.pg_container, args.migration, args.out or dump_root / "sdr-embeddings_v1"))
    elif args.command == "package-tech":
        asyncio.run(package_tech(args.dsn, args.pg_container, args.migrations, args.out or dump_root / "airbus-safety-first_v2"))
    else:
        asyncio.run(package_rag(args.dsn, args.pg_container, args.migrations, args.out or dump_root / "sdr-jasc-amm_v2"))


if __name__ == "__main__":
    main()
