"""Real MCP/SQL scope tests with synthetic independent vectors, not an accuracy evaluation."""
import copy

import pytest
from conftest import FixedQueryEncoder, hybrid_caller, make_context, seed_sdr_vectors
from sqlalchemy import update

from mro_mcp.common.errors import ToolFailure
from mro_mcp.common.scope import model_policy
from mro_rag import db

POLICY = {"policy_version": "airbus-model-scope-v1", "default_level": "MODEL_GENERATION",
          "levels": {"EXACT": ["A320214"], "MODEL_GENERATION": ["A320214", "A320232"],
                     "FAMILY": ["A320214", "A320232", "A321211"]}}

@pytest.mark.parametrize("query", ["wheel vibration", "바퀴 진동"])
async def test_both_rankings_expand_and_r05_revalidates_authorized_models(setup_db, query):
    owner, _, _, rid, _ = setup_db
    async with owner.begin() as conn:
        await conn.execute(update(db.sdr).where(db.sdr.c.release_id == rid, db.sdr.c.case_id == "SDR-TEST-2").values(model_key="A320232"))
        await conn.execute(update(db.sdr).where(db.sdr.c.release_id == rid, db.sdr.c.case_id == "SDR-TEST-3").values(model_key="A321211"))
    try:
        await seed_sdr_vectors(owner, rid)
        claims, args = await make_context(owner, rid, operator=None, model_scope=POLICY)
        async with await hybrid_caller(FixedQueryEncoder()) as svc:
            default = await svc.invoke("search_sdr_cases", dict(args, query=query), claims)
            assert default["status"] == "OK", default
            assert {r["model_key"] for r in default["items"]} == {"A320214", "A320232"}
            assert default["applied_scope"]["search_level"] == "MODEL_GENERATION"
            exact = await svc.invoke("search_sdr_cases", dict(args, query=query, scope_level="EXACT"), claims)
            assert {r["model_key"] for r in exact["items"]} == {"A320214"}
            family = await svc.invoke("search_sdr_cases", dict(args, query=query, scope_level="FAMILY", limit=1), claims)
            bad_page = await svc.invoke("search_sdr_cases", dict(args, query=query, scope_level="EXACT", limit=1, cursor=family["next_cursor"]), claims)
            assert bad_page["error"]["code"] == "INVALID_CURSOR"
            family = await svc.invoke("search_sdr_cases", dict(args, query=query, scope_level="FAMILY"), claims)
            assert {r["model_key"] for r in family["items"]} == {"A320214", "A320232", "A321211"}
            for row in family["items"]:
                original = await svc.invoke("get_evidence", dict(args, evidence_id=row["evidence_id"]), claims)
                assert original["status"] == "OK", original
                for key in ("model_key", "requested_model_key", "model_relation", "configuration_match"):
                    assert original["result"][key] == row[key]
            references = await svc.invoke("get_sdr_amm_references", dict(args, evidence_ids=["EV-SDR-TEST-3"]), claims)
            assert references["status"] == "OK" and references["items"][0]["model_relation"] == "FAMILY"
            old_claims, old_args = await make_context(owner, rid, operator=None)
            old = await svc.invoke("search_sdr_cases", dict(old_args, query=query), old_claims)
            assert {r["model_key"] for r in old["items"]} == {"A320214"}
            denied = await svc.invoke("search_sdr_cases", dict(old_args, query=query, scope_level="FAMILY"), old_claims)
            assert denied["error"]["code"] == "FORBIDDEN"
            scoped_claims, scoped_args = await make_context(owner, rid, model_scope=POLICY)
            scoped = await svc.invoke("get_evidence", dict(scoped_args, evidence_id="EV-SDR-TEST-3"), scoped_claims)
            assert scoped["status"] != "OK"  # OTHER operator is still excluded.
            async with owner.begin() as conn:
                await conn.execute(update(db.sdr).where(db.sdr.c.release_id == rid, db.sdr.c.case_id == "SDR-TEST-3").values(model_key="A330343"))
            outside = await svc.invoke("get_evidence", dict(args, evidence_id="EV-SDR-TEST-3"), claims)
            assert outside["status"] != "OK"
    finally:
        async with owner.begin() as conn:
            await conn.execute(update(db.sdr).where(db.sdr.c.release_id == rid).values(model_key="A320214"))


def test_invalid_allowance_is_not_inferred_from_prefix():
    bad = copy.deepcopy(POLICY)
    bad["levels"]["EXACT"] = ["A330343"]
    with pytest.raises(ToolFailure):
        model_policy({"model_key": "A320214", "model_scope": bad})
