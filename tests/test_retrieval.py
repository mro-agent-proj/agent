import json

import pytest
from conftest import FixedEncoder, make_context
from sqlalchemy import insert, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from mro_rag import db
from mro_rag.contracts import TOOLS
from mro_rag.embedding import build_embeddings
from mro_rag.ingest import ingest, load_package


def test_catalog_readonly_and_schema_sync():
    assert set(TOOLS) == {"search_jasc_codes", "search_sdr_cases", "search_amm_references", "get_evidence"}
    assert all(t["annotations"]["readOnlyHint"] for t in TOOLS.values())
    with open("docs/spec-v1/mcp-tool-contracts.json") as f:
        documented = {t["mcp"]["name"]: t["mcp"] for t in json.load(f)["tools"]}
    assert all(value == documented[key] for key, value in TOOLS.items())


def test_source_tampering_rejected(package):
    file = package / "reference/jasc_codes.jsonl"
    original = file.read_bytes()
    try:
        file.write_bytes(original + b" ")
        with pytest.raises(ValueError, match="hash mismatch"):
            load_package(package)
    finally:
        file.write_bytes(original)


async def test_idempotent_ingestion_and_timestamp(setup_db, package):
    owner, _, _, rid, _ = setup_db
    again = await ingest(owner, package)
    assert again["reused"] is True
    async with owner.connect() as c:
        row = (
            (
                await c.execute(
                    select(db.sdr).where(db.sdr.c.release_id == rid, db.sdr.c.case_id == "SDR-TEST-1")
                )
            )
            .mappings()
            .one()
        )
        assert row["submission_date"].isoformat() == "2025-01-02"
        assert row["submission_at"].date().isoformat() == "2025-01-01"
        assert len(row["raw_record"]) == 76


async def test_jasc_null_and_invalid_and_global_scope(setup_db):
    owner, _, svc, rid, _ = setup_db
    claims, args = await make_context(owner, rid, operator=None, model=None)
    result = await svc.invoke("search_jasc_codes", dict(args, codes=["2924"]), claims)
    assert result["status"] == "OK" and result["items"][0]["definition"] is None
    assert result["warnings"] and not result["applied_scope"]["model_filter_applied"]
    bad = await svc.invoke("search_jasc_codes", dict(args, codes=["9999"]), claims)
    assert bad["error"]["code"] == "INVALID_JASC"
    missing = await svc.invoke("search_sdr_cases", dict(args, query="wheel"), claims)
    assert missing["status"] == "NEEDS_INPUT"


async def test_scope_cursor_and_evidence_roundtrip(setup_db):
    owner, _, svc, rid, _ = setup_db
    claims, args = await make_context(owner, rid)
    first = await svc.invoke("search_sdr_cases", dict(args, query="wheel", limit=1), claims)
    assert first["status"] == "OK" and first["counts"]["scope_count"] == 2
    second = await svc.invoke(
        "search_sdr_cases", dict(args, query="wheel", limit=1, cursor=first["next_cursor"]), claims
    )
    assert first["items"][0]["case_id"] != second["items"][0]["case_id"]
    assert second["next_cursor"] is None
    altered = await svc.invoke(
        "search_sdr_cases", dict(args, query="other", limit=1, cursor=first["next_cursor"]), claims
    )
    assert altered["error"]["code"] == "INVALID_CURSOR"
    tampered = await svc.invoke(
        "search_sdr_cases", dict(args, query="wheel", limit=1, cursor=first["next_cursor"] + "0"), claims
    )
    assert tampered["error"]["code"] == "INVALID_CURSOR"
    item = first["items"][0]
    evidence = await svc.invoke("get_evidence", dict(args, evidence_id=item["evidence_id"]), claims)
    assert evidence["result"]["text"] == item["narrative_excerpt"]
    assert evidence["result"]["locator"] == item["source_locator"]
    denied = await svc.invoke("get_evidence", dict(args, evidence_id="EV-SDR-TEST-3"), claims)
    assert denied["status"] == "NO_MATCH" and denied["result"] is None


async def test_fixed_jasc_is_filter_candidate_is_not(setup_db):
    owner, _, svc, rid, _ = setup_db
    claims, args = await make_context(owner, rid)
    broad = await svc.invoke("search_sdr_cases", dict(args, query="wheel", candidate_jasc=["2924"]), claims)
    assert len(broad["items"]) == 2 and broad["items"][0]["reported_jasc"] == "2924"
    locked, a2 = await make_context(
        owner, rid, filters={"jasc_codes": ["3210"], "difficulty_from": None, "difficulty_to": None}
    )
    result = await svc.invoke("search_sdr_cases", dict(a2, query="wheel", candidate_jasc=["2924"]), locked)
    assert {r["reported_jasc"] for r in result["items"]} == {"3210"}


async def test_status_auth_revocation_and_stale_version(setup_db):
    owner, _, svc, rid, _ = setup_db
    claims, args = await make_context(owner, rid)
    no_match = await svc.invoke("search_sdr_cases", dict(args, query="zznonexistent"), claims)
    assert no_match["status"] == "NO_MATCH"
    empty, a2 = await make_context(owner, rid, operator="UNKNOWN")
    no_data = await svc.invoke("search_sdr_cases", dict(a2, query="wheel"), empty)
    assert no_data["status"] == "NO_DATA"
    wrong = await svc.invoke("search_sdr_cases", dict(args, query="wheel"), dict(claims, sub="mallory"))
    assert wrong["error"]["code"] == "FORBIDDEN"
    old = await svc.invoke("search_sdr_cases", dict(args, context_version=2, query="wheel"), claims)
    assert old["error"]["code"] == "CONTEXT_VERSION_CONFLICT"
    async with owner.begin() as conn:
        await conn.execute(
            update(db.contexts).where(db.contexts.c.run_id == claims["run_id"]).values(active=False)
        )
    revoked = await svc.invoke("search_sdr_cases", dict(args, query="wheel"), claims)
    assert revoked["error"]["code"] == "FORBIDDEN"


async def test_amm_number_not_unique_and_quote_roundtrip(setup_db):
    owner, _, svc, rid, _ = setup_db
    claims, args = await make_context(owner, rid)
    result = await svc.invoke("search_amm_references", dict(args, amm_reference_exact="32.11.00"), claims)
    assert len(result["items"]) == 2
    assert len({r["evidence_id"] for r in result["items"]}) == 2
    for item in result["items"]:
        evidence = await svc.invoke("get_evidence", dict(args, evidence_id=item["evidence_id"]), claims)
        assert evidence["result"]["text"] == item["quote"]
        assert evidence["result"]["parent_source_ids"] == [item["source_case_id"]]


async def test_vector_index_pinned_and_scoped(setup_db):
    owner, _, svc, rid, release_key = setup_db
    claims, args = await make_context(owner, rid)
    # Explicitly emulate a run started before vector readiness even on a repeated test run.
    async with owner.begin() as c:
        keys = (
            (
                await c.execute(
                    select(db.indexes.c.index_key).where(
                        db.indexes.c.release_id == rid, db.indexes.c.method != "VECTOR"
                    )
                )
            )
            .scalars()
            .all()
        )
        await c.execute(
            update(db.contexts).where(db.contexts.c.run_id == claims["run_id"]).values(index_ids=list(keys))
        )
    missing = await svc.invoke("search_amm_references", dict(args, query="wheel vibration"), claims)
    assert missing["error"]["code"] == "INDEX_NOT_READY"
    await build_embeddings(owner, release_key, FixedEncoder())
    still_pinned = await svc.invoke("search_amm_references", dict(args, query="wheel vibration"), claims)
    assert still_pinned["error"]["code"] == "INDEX_NOT_READY"
    fresh, a2 = await make_context(owner, rid)
    result = await svc.invoke(
        "search_amm_references", dict(a2, query="wheel vibration", amm_reference_exact="32-11-00"), fresh
    )
    assert result["status"] == "OK" and len(result["items"]) == 2
    assert all(r["score"] > 0.99 for r in result["items"])
    assert all(r["source_case_id"] != "SDR-TEST-3" for r in result["items"])


async def test_db_readonly_and_cross_parent_fk(setup_db):
    owner, reader, _, rid, _ = setup_db
    with pytest.raises(DBAPIError):
        async with reader.begin() as c:
            await c.execute(text("DELETE FROM public.jasc_codes WHERE false"))
    async with owner.connect() as c:
        a = (
            (
                await c.execute(
                    select(db.amm).where(db.amm.c.release_id == rid).order_by(db.amm.c.distilled_record_id)
                )
            )
            .mappings()
            .all()
        )
        chunk = (
            (
                await c.execute(
                    select(db.chunks).where(db.chunks.c.release_id == rid, db.chunks.c.chunk_id == "CHUNK-1")
                )
            )
            .mappings()
            .one()
        )
    with pytest.raises(IntegrityError):
        async with owner.begin() as c:
            await c.execute(
                insert(db.links).values(
                    release_id=rid,
                    sdr_case_uuid=chunk["sdr_case_uuid"],
                    chunk_uuid=chunk["id"],
                    reference_uuid=a[1]["id"],
                )
            )


async def test_context_publication_rejects_rollback_and_revokes_previous(setup_db):
    from uuid import uuid4

    from mro_rag.context import publish_context

    owner, _, svc, rid, _ = setup_db
    claims, args = await make_context(owner, rid)
    kwargs = dict(
        request_id=claims["request_id"],
        subject_id="alice",
        tenant_id="tenant-a",
        operator_code="TESTA",
        model_key="A320214",
        release_id=rid,
        mapping_revision="synthetic-v1",
    )
    async with owner.begin() as conn:
        await publish_context(conn, run_id=uuid4().hex, context_version=2, **kwargs)
    with pytest.raises(ValueError, match="must increase"):
        async with owner.begin() as conn:
            await publish_context(conn, run_id=uuid4().hex, context_version=1, **kwargs)
    old = await svc.invoke("search_sdr_cases", dict(args, query="wheel"), claims)
    assert old["error"]["code"] == "FORBIDDEN"
    async with owner.connect() as conn:
        active = (
            (
                await conn.execute(
                    select(db.contexts.c.context_version).where(
                        db.contexts.c.request_id == claims["request_id"], db.contexts.c.active.is_(True)
                    )
                )
            )
            .scalars()
            .all()
        )
    assert active == [2]


def test_flyway_and_local_migration_match():
    import ast
    from pathlib import Path

    tree = ast.parse(Path("migrations/versions/0001_rag.py").read_text())
    upgrade = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "upgrade")
    statements = [
        ast.literal_eval(n.value.args[0]).strip().rstrip(";")
        for n in upgrade.body
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
    ]
    sql = Path("integration/backend/V3__rag_evidence.sql").read_text()
    actual = "\n".join(line for line in sql.splitlines() if not line.startswith("--")).strip()
    assert actual == ";\n\n".join(statements) + ";"
