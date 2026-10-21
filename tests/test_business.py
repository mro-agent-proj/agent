"""business-mcp (B01~B03). DB-backed cases run only when BUSINESS_TEST_* variables point at a seeded backend DB."""

import os

import pytest

from mro_business.contracts import TOOLS
from mro_business.service import (
    STATUS_MAP,
    BusinessService,
    decode_cursor,
    encode_cursor,
    history_filter_key,
)

REQUEST_ID = "2203f693-f8b5-463b-bf56-b93584ef15a8"
TEAM_STATUSES = {
    "QUEUED",
    "CLASSIFYING",
    "INVESTIGATING",
    "REVIEWING",
    "ASSIGNED",
    "ACCEPTED",
    "HUMAN_REVIEW",
    "CLOSED",
}


def test_contracts_cover_read_only_business_tools():
    assert set(TOOLS) == {"get_request_context", "get_aircraft_history", "search_departments"}
    for tool in TOOLS.values():
        assert tool["annotations"]["readOnlyHint"] is True
        assert tool["description"]
    status = TOOLS["get_request_context"]["outputSchema"]["properties"]["result"]["anyOf"][0]["properties"][
        "status"
    ]
    assert set(STATUS_MAP.values()) <= set(status["enum"])


def test_every_team_status_is_mapped():
    assert set(STATUS_MAP) == TEAM_STATUSES


def test_cursor_round_trip_and_binding():
    key = history_filter_key(REQUEST_ID, {"occurred_from": "2026-09-21"})
    assert (
        decode_cursor(encode_cursor({"k": key, "t": "2026-10-05T16:37:00+00:00", "id": "MH-001440"}))["k"]
        == key
    )
    assert key != history_filter_key(REQUEST_ID, {})


@pytest.mark.parametrize(
    ("name", "args", "code"),
    [
        ("get_request_context", {"request_id": "REQ-MOCK-001"}, "INVALID_ARGUMENT"),
        ("get_request_context", {}, "INVALID_ARGUMENT"),
        (
            "get_aircraft_history",
            {"request_id": REQUEST_ID, "context_version": 2},
            "CONTEXT_VERSION_CONFLICT",
        ),
        (
            "get_aircraft_history",
            {"request_id": REQUEST_ID, "context_version": 1, "limit": 101},
            "INVALID_ARGUMENT",
        ),
        (
            "get_aircraft_history",
            {"request_id": REQUEST_ID, "context_version": 1, "query": " "},
            "INVALID_ARGUMENT",
        ),
        (
            "search_departments",
            {"request_id": REQUEST_ID, "context_version": 1, "candidate_jasc": ["21"]},
            "INVALID_ARGUMENT",
        ),
    ],
)
async def test_invalid_input_fails_before_db(name, args, code):
    out = await BusinessService(engine=None).invoke(name, args)
    assert out["status"] == "ERROR"
    assert out["error"]["code"] == code
    assert out.get("items", []) == [] and out.get("result") is None


DB_URL = os.environ.get("BUSINESS_TEST_DATABASE_URL")
DB_REQUEST = os.environ.get("BUSINESS_TEST_REQUEST_ID")
needs_db = pytest.mark.skipif(
    not (DB_URL and DB_REQUEST), reason="BUSINESS_TEST_DATABASE_URL/REQUEST_ID not set"
)


@pytest.fixture
async def service():
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(DB_URL)
    yield BusinessService(engine)
    await engine.dispose()


@needs_db
async def test_seeded_db_round_trip(service):
    context = await service.invoke("get_request_context", {"request_id": DB_REQUEST})
    assert context["status"] == "OK", context["error"]
    assert context["result"]["resolved_scope"]["model_key"]

    args = {"request_id": DB_REQUEST, "context_version": context["context_version"], "limit": 2}
    first = await service.invoke("get_aircraft_history", args)
    assert first["status"] in {"OK", "NO_DATA"}
    if first["next_cursor"]:
        second = await service.invoke("get_aircraft_history", {**args, "cursor": first["next_cursor"]})
        assert {i["history_id"] for i in first["items"]}.isdisjoint(i["history_id"] for i in second["items"])

    departments = await service.invoke(
        "search_departments",
        {"request_id": DB_REQUEST, "context_version": 1, "candidate_jasc": ["3230"], "limit": 7},
    )
    assert departments["items"][0]["department_id"] == "HYD"
    missing = await service.invoke(
        "get_request_context", {"request_id": "00000000-0000-4000-8000-000000000000"}
    )
    assert missing["error"]["code"] == "NOT_FOUND"
