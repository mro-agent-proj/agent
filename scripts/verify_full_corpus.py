"""Opt-in verification against a locally loaded corpus; not an accuracy benchmark."""

import asyncio
import json
import os
import time
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, select, text

from mro_mcp.rag.local import LocalCaller
from mro_mcp.rag.server import create_server
from mro_rag import db
from mro_rag.context import publish_context
from mro_rag.embedding import CONFIG, LocalEncoder


async def main():
    owner = db.engine(os.environ["DATABASE_URL"])
    encoder = LocalEncoder()
    server = create_server(
        database_url=os.environ["RAG_DATABASE_URL"],
        jwt_secret="local-verification-jwt-secret-" * 2,
        cursor_secret="local-verification-cursor-secret-" * 2,
        encoder=encoder,
    )
    svc = LocalCaller(server)
    await svc.__aenter__()
    run = "verification-" + uuid4().hex
    try:
        async with owner.begin() as conn:
            release = (
                (await conn.execute(select(db.releases).where(db.releases.c.state == "READY")))
                .mappings()
                .one()
            )
            rid = release["id"]
            scope = (
                await conn.execute(
                    select(db.sdr.c.operator_code, db.sdr.c.model_key, func.count().label("n"))
                    .where(db.sdr.c.release_id == rid)
                    .group_by(db.sdr.c.operator_code, db.sdr.c.model_key)
                    .order_by(text("n DESC"))
                    .limit(1)
                )
            ).one()
            counts = {
                table.name: await conn.scalar(
                    select(func.count()).select_from(table).where(table.c.release_id == rid)
                )
                for table in (db.jasc, db.sdr, db.amm, db.chunks, db.evidence, db.embeddings)
            }
            await publish_context(
                conn,
                run_id=run,
                request_id=run,
                context_version=1,
                subject_id="local-verifier",
                tenant_id="local-verification",
                operator_code=scope[0],
                model_key=scope[1],
                release_id=rid,
                mapping_revision="raw-code-verification-only",
                seconds=300,
            )
            inputs = (
                (
                    await conn.execute(
                        select(db.chunks.c.embedding_text).where(db.chunks.c.release_id == rid).distinct()
                    )
                )
                .scalars()
                .all()
            )
        # Reproduce window construction and verify that no saved embedding silently lost a token tail.
        maximum, window_count = 0, 0
        tokenizer = encoder.model.tokenizer
        for value in inputs:
            ids = tokenizer.encode(value, add_special_tokens=False, verbose=False)
            if len(ids) <= CONFIG["window_tokens"]:
                windows = [CONFIG["document_prefix"] + value]
            else:
                windows = []
                for offset in range(0, len(ids), CONFIG["stride_tokens"]):
                    windows.append(
                        CONFIG["document_prefix"]
                        + tokenizer.decode(
                            ids[offset : offset + CONFIG["window_tokens"]], skip_special_tokens=True
                        )
                    )
                    if offset + CONFIG["window_tokens"] >= len(ids):
                        break
            lengths = [len(v) for v in tokenizer(windows, truncation=False, verbose=False)["input_ids"]]
            maximum = max(maximum, *lengths)
            window_count += len(windows)
        assert maximum <= encoder.model.max_seq_length, maximum
        claims = dict(
            run_id=run,
            request_id=run,
            context_version=1,
            sub="local-verifier",
            tenant_id="local-verification",
        )
        args = dict(request_id=run, context_version=1)
        cases = []
        for query in ("착륙 장치 바퀴 진동과 타이어 손상", "hydraulic system fluid leak"):
            start = time.monotonic()
            result = await svc.invoke("search_amm_references", dict(args, query=query, limit=3), claims)
            assert result["status"] == "OK", result
            for item in result["items"]:
                evidence = await svc.invoke(
                    "get_evidence", dict(args, evidence_id=item["evidence_id"]), claims
                )
                assert evidence["status"] == "OK" and evidence["result"]["text"] == item["quote"]
                assert evidence["result"]["locator"] == item["locator"]
            cases.append(
                dict(
                    query=query,
                    returned=len(result["items"]),
                    seconds=round(time.monotonic() - start, 3),
                    evidence_roundtrip=True,
                    evidence_ids=[i["evidence_id"] for i in result["items"]],
                )
            )
        report = dict(
            release_key=release["release_key"],
            counts=counts,
            model=CONFIG,
            scope=dict(operator_code=scope[0], model_key=scope[1], mapping="raw-code-verification-only"),
            token_window_check=dict(
                unique_inputs=len(inputs),
                windows=window_count,
                maximum_tokens=maximum,
                model_limit=encoder.model.max_seq_length,
            ),
            cases=cases,
            limitation="Integration smoke test, not retrieval relevance or diagnostic accuracy evaluation",
        )
        Path("reports/runtime-verification.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        async with owner.begin() as conn:
            await conn.execute(db.contexts.update().where(db.contexts.c.run_id == run).values(active=False))
        await owner.dispose()
        await svc.__aexit__()


if __name__ == "__main__":
    asyncio.run(main())
