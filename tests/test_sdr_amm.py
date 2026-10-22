"""SDR -> AMM handoff lookup: only AMM numbers cited in in-scope SDR evidence, never a search."""

from conftest import make_context


async def test_sdr_amm_references_follow_scope(setup_db):
    owner, _, svc, rid, _ = setup_db
    ids = ["EV-SDR-TEST-1", "EV-SDR-TEST-3"]

    claims, args = await make_context(owner, rid)  # operator TESTA: SDR-TEST-3 (OTHER) is out of scope
    result = await svc.invoke("get_sdr_amm_references", dict(args, evidence_ids=ids), claims)
    assert result["status"] == "OK"
    assert result["counts"] == {"scope_count": 2, "eligible_count": 1, "returned_count": 1}
    item = result["items"][0]
    assert item["sdr_evidence_id"] == "EV-SDR-TEST-1" and item["evidence_id"] == "EV-AMM-TEST-1"
    assert item["cited_amm_ref_normalized"] == "32-11-00" and item["score_kind"] == "exact"
    assert any(w == "SDR_NOT_IN_SCOPE: EV-SDR-TEST-3" for w in result["warnings"])
    assert any(w.startswith("AMM_REFERENCE_ONLY") for w in result["warnings"])

    wide_claims, wide_args = await make_context(owner, rid, operator=None)
    wide = await svc.invoke("get_sdr_amm_references", dict(wide_args, evidence_ids=ids), wide_claims)
    assert [i["sdr_evidence_id"] for i in wide["items"]] == ids

    none = await svc.invoke("get_sdr_amm_references", dict(args, evidence_ids=["EV-SDR-TEST-9"]), claims)
    assert none["status"] == "NO_MATCH" and none["items"] == []

    bad = await svc.invoke("get_sdr_amm_references", dict(args, evidence_ids=["EV-AMM-TEST-1"]), claims)
    assert bad["status"] == "ERROR"
