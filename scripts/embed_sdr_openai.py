#!/usr/bin/env python3
"""SDR 원문 임베딩(OpenAI text-embedding-3-large, 1024차원)을 만들고 Backend 전달본으로 묶습니다.

  # 1) 비어 있는 행만 임베딩 (중단돼도 다시 실행하면 이어서 함). 대상 DB에 Backend V7이 적용돼 있어야 함
  OPENAI_API_KEY=... uv run python scripts/embed_sdr_openai.py embed --dsn postgresql://mro:mro@127.0.0.1:5432/mro
  # 2) data-only 덤프 + manifest + verify.sql + SHA256SUMS
  uv run python scripts/embed_sdr_openai.py package --dsn ... --pg-container backend-postgres-1 \\
      --migration ../backend/src/main/resources/db/migration/V7__rag_sdr_embeddings.sql --out ../backend/data/rag-dump/sdr-embeddings_v1

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


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["embed", "package"])
    ap.add_argument("--dsn", default=os.environ.get("EMBED_DSN", "postgresql://mro:mro@127.0.0.1:5432/mro"))
    ap.add_argument("--limit", type=int, help="embed: 시험용 상한")
    ap.add_argument("--pg-container", default="backend-postgres-1")
    ap.add_argument("--migration", default="../backend/src/main/resources/db/migration/V7__rag_sdr_embeddings.sql")
    ap.add_argument("--out", type=Path, default=Path("../backend/data/rag-dump/sdr-embeddings_v1"))
    args = ap.parse_args()
    if args.command == "embed":
        key = os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY")
        if not key:
            sys.exit("OPENAI_API_KEY가 필요합니다")
        asyncio.run(embed(args.dsn, key, args.limit))
    else:
        asyncio.run(package(args.dsn, args.pg_container, args.migration, args.out))


if __name__ == "__main__":
    main()
