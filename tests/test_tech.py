"""R04 search_technical_docs: family applicability, unknown handling, and R05 re-reading of EV-DOC-*."""

from conftest import FixedQueryEncoder, hybrid_caller, make_context, seed_tech

from mro_mcp.rag import tech


def test_family_applicability():
    assert tech.family("A320214") == "A320" and tech.family("A321271N") == "A320"
    assert tech.family("A330243") == "A330" and tech.family("BD5001A10") is None
    assert tech.applies("A320214", "On the A320 Family and A330, the bleed is pneumatic") is True
    assert tech.applies("A321271N", "Seen on an A321neo after cabin work") is True
    assert tech.applies("A320214", "All Airbus aircraft, except the A380, have two RAs") is True
    assert tech.applies("A320214", "A330-300 trimmable horizontal stabilizer damage") is False
    assert tech.applies("A330243", "This applies to the A320 Family only") is False
    assert tech.applies("A320214", "Corrosion: a potential safety issue") is None


async def test_technical_docs_scope_and_evidence(setup_db):
    owner, _, _, rid, _ = setup_db
    await seed_tech(owner)
    claims, args = await make_context(owner, rid, operator=None, model="A320214")
    async with await hybrid_caller(FixedQueryEncoder()) as svc:
        await check_scope_and_evidence(svc, owner, rid, claims, args)


async def check_scope_and_evidence(svc, owner, rid, claims, args):

    result = await svc.invoke("search_technical_docs", dict(args, query="wheel vibration"), claims)
    assert result["status"] == "OK"
    docs = [item["document_id"] for item in result["items"]]
    # Other families, flight-ops articles and articles naming no aircraft stay out by default.
    assert docs[0] == "DOC-T-A320" and set(docs) == {"DOC-T-A320", "DOC-T-FLEET"}
    assert result["applied_scope"]["model_filter_applied"] is True
    assert result["applied_scope"]["operator_filter_applied"] is False
    assert result["snapshot_ids"][0].startswith("TECH-")
    assert result["counts"] == {"scope_count": 2, "eligible_count": 2, "returned_count": 2}
    assert result["items"][0]["score_kind"] == "vector"
    assert result["warnings"][0].startswith("TECHNICAL_REFERENCE_ONLY")

    wide = await svc.invoke(
        "search_technical_docs",
        dict(args, query="wheel", system_keywords=["corrosion"], include_unknown_applicability=True),
        claims,
    )
    by_doc = {item["document_id"]: item for item in wide["items"]}
    assert by_doc["DOC-T-NOTYPE"]["applicability_status"] == "unknown"
    assert set(by_doc) == {"DOC-T-A320", "DOC-T-FLEET", "DOC-T-NOTYPE"}
    assert wide["items"][0]["score_kind"] == "fusion"
    assert any(w.startswith("APPLICABILITY_UNKNOWN") for w in wide["warnings"])

    first = result["items"][0]
    evidence = await svc.invoke("get_evidence", dict(args, evidence_id=first["evidence_id"]), claims)
    assert evidence["status"] == "OK"
    assert evidence["result"]["source_type"] == "airbus_technical_article"
    assert evidence["result"]["text"] == first["quote"]
    assert evidence["result"]["locator"] == first["locator"]
    # The AI pins technical snapshots separately; R04 and R05 must agree.
    assert evidence["snapshot_ids"] == result["snapshot_ids"]

    other_claims, other_args = await make_context(owner, rid, operator=None, model="A330243")
    other = await svc.invoke("search_technical_docs", dict(other_args, query="wheel"), other_claims)
    # The fleet-wide article applies to every family; the A320-only article does not.
    assert [item["document_id"] for item in other["items"]] == ["DOC-T-A330", "DOC-T-FLEET"]
    hidden = await svc.invoke("get_evidence", dict(other_args, evidence_id=first["evidence_id"]), other_claims)
    assert hidden["status"] == "NO_MATCH"


async def test_technical_docs_fall_back_to_lexical(setup_db):
    owner, _, plain, rid, _ = setup_db
    await seed_tech(owner)
    claims, args = await make_context(owner, rid, operator=None, model="A320214")
    result = await plain.invoke("search_technical_docs", dict(args, query="wheel vibration"), claims)
    assert result["status"] == "OK"
    # Lexical match only: the A320 article names wheel and vibration, the fleet-wide radio altimeter one does not.
    assert [i["document_id"] for i in result["items"]] == ["DOC-T-A320"]
    assert result["items"][0]["score_kind"] == "lexical"
    assert any(w.startswith("VECTOR_UNAVAILABLE: no query encoder") for w in result["warnings"])


async def test_technical_docs_without_stored_vectors_fall_back_to_lexical(setup_db):
    """A working query encoder must not hide missing article vectors as NO_MATCH."""
    from sqlalchemy import delete

    from mro_mcp.rag import tech

    owner, _, _, rid, _ = setup_db
    await seed_tech(owner)
    claims, args = await make_context(owner, rid, operator=None, model="A320214")
    async with owner.begin() as conn:
        await conn.execute(delete(tech.embeddings).where(tech.embeddings.c.chunk_id == "DOC-T-FLEET-01"))
    async with await hybrid_caller(FixedQueryEncoder()) as svc:
        partial = await svc.invoke("search_technical_docs", dict(args, query="wheel vibration"), claims)
        assert partial["items"][0]["score_kind"] == "vector"
        assert any(w.startswith("VECTOR_PARTIAL: 1 of 2") for w in partial["warnings"])
        async with owner.begin() as conn:
            await conn.execute(delete(tech.embeddings))
        missing = await svc.invoke("search_technical_docs", dict(args, query="wheel vibration"), claims)
        assert missing["status"] == "OK" and [i["document_id"] for i in missing["items"]] == ["DOC-T-A320"]
        assert missing["items"][0]["score_kind"] == "lexical"
        assert any(w.startswith("VECTOR_UNAVAILABLE: article embeddings are not restored") for w in missing["warnings"])
