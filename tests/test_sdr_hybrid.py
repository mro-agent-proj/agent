"""R02 hybrid: lexical + semantic rankings fused inside the same scope, lexical-only when vectors are unavailable."""

from conftest import FixedQueryEncoder, hybrid_caller, make_context, seed_sdr_vectors


async def test_sdr_search_fuses_rankings_and_answers_korean(setup_db):
    owner, _, _, rid, _ = setup_db
    await seed_sdr_vectors(owner, rid)
    claims, args = await make_context(owner, rid, operator=None)
    async with await hybrid_caller(FixedQueryEncoder()) as svc:
        english = await svc.invoke("search_sdr_cases", dict(args, query="wheel vibration"), claims)
        assert english["status"] == "OK" and english["items"][0]["score_kind"] == "fusion"
        assert {i["case_id"] for i in english["items"]} == {"SDR-TEST-1", "SDR-TEST-2", "SDR-TEST-3"}
        assert not any(w.startswith("VECTOR_UNAVAILABLE") for w in english["warnings"])

        # Korean has no lexical match at all; the semantic half still finds the wheel cases first.
        korean = await svc.invoke("search_sdr_cases", dict(args, query="바퀴 진동"), claims)
        assert korean["status"] == "OK"
        assert korean["items"][-1]["case_id"] == "SDR-TEST-2"

        # Exact phrase mode stays lexical.
        exact = await svc.invoke("search_sdr_cases", dict(args, query="wheel vibration", search_mode="exact"), claims)
        assert exact["items"][0]["score_kind"] == "lexical"

        # Scope still applies to the semantic half: operator TESTA hides SDR-TEST-3 (OTHER).
        scoped_claims, scoped_args = await make_context(owner, rid)
        scoped = await svc.invoke("search_sdr_cases", dict(scoped_args, query="바퀴 진동"), scoped_claims)
        assert {i["case_id"] for i in scoped["items"]} == {"SDR-TEST-1", "SDR-TEST-2"}


async def test_sdr_search_falls_back_to_lexical(setup_db):
    owner, _, plain, rid, _ = setup_db
    await seed_sdr_vectors(owner, rid)
    claims, args = await make_context(owner, rid, operator=None)
    without = await plain.invoke("search_sdr_cases", dict(args, query="wheel vibration"), claims)
    assert without["items"][0]["score_kind"] == "lexical"
    assert any(w.startswith("VECTOR_UNAVAILABLE: no query encoder") for w in without["warnings"])
    async with await hybrid_caller(FixedQueryEncoder(fail=True)) as svc:
        broken = await svc.invoke("search_sdr_cases", dict(args, query="wheel vibration"), claims)
        assert broken["status"] == "OK" and broken["items"][0]["score_kind"] == "lexical"
        assert any("query embedding failed (TimeoutError)" in w for w in broken["warnings"])


async def test_cursor_rejects_vector_outage_instead_of_switching_rankings(setup_db):
    owner, _, _, rid, _ = setup_db
    await seed_sdr_vectors(owner, rid)
    claims, args = await make_context(owner, rid, operator=None)
    encoder = FixedQueryEncoder()
    async with await hybrid_caller(encoder) as svc:
        args = dict(args, query="wheel vibration", limit=1)
        first = await svc.invoke("search_sdr_cases", args, claims)
        second = await svc.invoke("search_sdr_cases", {**args, "cursor": first["next_cursor"]}, claims)
        assert second["status"] == "OK"
        assert first["items"][0]["case_id"] != second["items"][0]["case_id"]
        encoder.fail = True
        changed = await svc.invoke("search_sdr_cases", {**args, "cursor": first["next_cursor"]}, claims)
        assert changed["error"]["code"] == "INVALID_CURSOR"
        fallback = await svc.invoke("search_sdr_cases", args, claims)
        assert fallback["status"] == "OK"
        encoder.fail = False
        changed = await svc.invoke("search_sdr_cases", {**args, "cursor": fallback["next_cursor"]}, claims)
        assert changed["error"]["code"] == "INVALID_CURSOR"
