"""business-mcp over the real MCP protocol (in-memory session).

Every response is also checked against the spec v1.0 JSON Schema (mro_rag/tool_contracts.json) so the
Pydantic models cannot drift from the contract. DB-backed cases run only when BUSINESS_TEST_* is set.
"""

import copy
import json
import os
from importlib.resources import files

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from mcp.shared.memory import create_connected_server_and_client_session

from mro_mcp.business import auth
from mro_mcp.business.server import create_server
from mro_mcp.business.tools import STATUS_MAP, decode_cursor, encode_cursor, history_filter_key

REQUEST_ID = "2203f693-f8b5-463b-bf56-b93584ef15a8"
TEAM_STATUSES = {
    "QUEUED",
    "CLASSIFYING",
    "INVESTIGATING",
    "REVIEWING",
    "ASSIGNED",
    "ACCEPTED",
    "HUMAN_REVIEW",
}
TEAM_STATUSES |= {"CLOSED"}


def legacy_validators():
    catalog = json.loads(files("mro_rag").joinpath("tool_contracts.json").read_text())
    validators = {}
    for tool in catalog["tools"]:
        if tool["id"] in {"B01", "B02", "B03"}:
            schema = copy.deepcopy(tool["mcp"]["outputSchema"])
            if tool["id"] == "B01":
                status = schema["properties"]["result"]["anyOf"][0]["properties"]["status"]
                status["enum"].append("CLOSED")  # team v0.3 state, documented in the PR
            validators[tool["mcp"]["name"]] = Draft202012Validator(schema, format_checker=FormatChecker())
    return validators


LEGACY = legacy_validators()


JWT_SECRET = "business-test-secret-" * 2


def claims_for(request_id):
    """What BearerAuthMiddleware sets from a backend token issued for this request."""
    return {"sub": "reporter", "tenant_id": "KAL", "run_id": "run-1", "request_id": str(request_id), "context_version": 1}


async def call(mcp, name, args, claims="from-args"):
    if claims == "from-args":
        claims = claims_for(args.get("request_id", REQUEST_ID))
    token = auth.principal.set(claims)
    try:
        async with create_connected_server_and_client_session(mcp._mcp_server) as session:
            result = await session.call_tool(name, args)
    finally:
        auth.principal.reset(token)
    LEGACY[name].validate(result.structuredContent)
    return result


@pytest.fixture
def offline_server():
    return create_server(database_url="postgresql+asyncpg://unused@127.0.0.1:1/unused", jwt_secret=JWT_SECRET)


def test_server_requires_a_signing_secret():
    with pytest.raises(ValueError, match="BUSINESS_JWT_SECRET"):
        create_server(database_url="postgresql+asyncpg://unused@127.0.0.1:1/unused", jwt_secret="short")


@pytest.mark.parametrize(
    "name, args",
    [
        ("get_request_context", {"request_id": REQUEST_ID}),
        ("get_aircraft_history", {"request_id": REQUEST_ID, "context_version": 1}),
        ("search_departments", {"request_id": REQUEST_ID, "context_version": 1}),
    ],
)
async def test_tools_read_only_the_request_in_the_token(offline_server, name, args):
    for claims in (None, claims_for("00000000-0000-4000-8000-000000000000")):
        result = await call(offline_server, name, args, claims=claims)
        assert result.isError is True
        assert result.structuredContent["error"]["code"] == "FORBIDDEN"


async def test_http_requires_a_backend_token_for_this_audience():
    """The middleware runs before the MCP app: no token, a rag token or a bad signature are all 401."""
    import httpx
    import jwt as pyjwt

    from mro_rag.context import issue_token

    seen = []

    async def app(scope, receive, send):
        seen.append(auth.principal.get())
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    cls, kwargs = auth.middleware(JWT_SECRET)
    transport = httpx.ASGITransport(app=cls(app, **kwargs))
    base = dict(subject_id="reporter", tenant_id="KAL", run_id="run-1", request_id=REQUEST_ID, context_version=1)
    good = issue_token(JWT_SECRET, **base, audience=auth.AUDIENCE)
    rag = issue_token(JWT_SECRET, **base)
    forged = issue_token("another-secret-" * 3, **base, audience=auth.AUDIENCE)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        assert (await client.get("/health")).status_code == 200
        assert (await client.post("/mcp")).status_code == 401
        for token in (rag, forged, "not-a-jwt"):
            assert (await client.post("/mcp", headers={"Authorization": "Bearer " + token})).status_code == 401
        assert (await client.post("/mcp", headers={"Authorization": "Bearer " + good})).status_code == 200
    assert seen[-1]["request_id"] == REQUEST_ID
    assert pyjwt.decode(good, options={"verify_signature": False})["aud"] == "mro-business"


async def test_tools_are_read_only_with_generated_schemas(offline_server):
    async with create_connected_server_and_client_session(offline_server._mcp_server) as session:
        tools = {t.name: t for t in (await session.list_tools()).tools}
    assert set(tools) == {"get_request_context", "get_aircraft_history", "search_departments"}
    for tool in tools.values():
        assert tool.annotations.readOnlyHint is True
        assert tool.outputSchema["type"] == "object"
    assert tools["get_aircraft_history"].inputSchema["required"] == ["request_id", "context_version"]


def test_every_team_status_is_mapped():
    assert set(STATUS_MAP) == TEAM_STATUSES


def test_cursor_round_trip_and_binding():
    key = history_filter_key(REQUEST_ID, "2026-09-21", None, None)
    assert (
        decode_cursor(encode_cursor({"k": key, "t": "2026-10-05T16:37:00+00:00", "id": "MH-1"}))["k"] == key
    )
    assert key != history_filter_key(REQUEST_ID, None, None, None)


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
        (
            "get_aircraft_history",
            {"request_id": REQUEST_ID, "context_version": 1, "tail_number": "HL7702"},
            "INVALID_ARGUMENT",
        ),
    ],
)
async def test_input_errors_use_the_spec_envelope(offline_server, name, args, code):
    result = await call(offline_server, name, args)
    out = result.structuredContent
    assert result.isError is True
    assert out["status"] == "ERROR" and out["error"]["code"] == code
    assert out.get("items", []) == [] and out.get("result") is None


DB_URL = os.environ.get("BUSINESS_TEST_DATABASE_URL")
DB_REQUEST = os.environ.get("BUSINESS_TEST_REQUEST_ID")
needs_db = pytest.mark.skipif(
    not (DB_URL and DB_REQUEST), reason="BUSINESS_TEST_DATABASE_URL/REQUEST_ID not set"
)


@pytest.fixture
async def server():
    mcp = create_server(database_url=DB_URL, jwt_secret=JWT_SECRET)
    await mcp.database.startup()
    yield mcp
    await mcp.database.shutdown()


@needs_db
async def test_seeded_db_round_trip(server):
    context = (await call(server, "get_request_context", {"request_id": DB_REQUEST})).structuredContent
    assert context["status"] == "OK", context["error"]
    assert context["result"]["resolved_scope"]["model_key"]

    args = {"request_id": DB_REQUEST, "context_version": context["context_version"], "limit": 2}
    first = (await call(server, "get_aircraft_history", args)).structuredContent
    assert first["status"] in {"OK", "NO_DATA"}
    if first["next_cursor"]:
        second = (
            await call(server, "get_aircraft_history", {**args, "cursor": first["next_cursor"]})
        ).structuredContent
        assert {i["history_id"] for i in first["items"]}.isdisjoint(i["history_id"] for i in second["items"])

    departments = await call(
        server,
        "search_departments",
        {"request_id": DB_REQUEST, "context_version": 1, "candidate_jasc": ["3230"], "limit": 7},
    )
    assert departments.structuredContent["items"][0]["department_id"] == "HYD"
    missing = await call(
        server, "get_request_context", {"request_id": "00000000-0000-4000-8000-000000000000"}
    )
    assert missing.isError and missing.structuredContent["error"]["code"] == "NOT_FOUND"


async def test_unexpected_failure_is_internal_error_without_details(offline_server, caplog):
    # The offline server never started its DB, so the lookup itself fails.
    result = await call(offline_server, "get_request_context", {"request_id": REQUEST_ID})
    out = result.structuredContent
    assert result.isError is True
    assert out["error"] == {
        "code": "INTERNAL_ERROR",
        "message": "Lookup failed; use trace_id to investigate",
        "retryable": True,
        "retry_after_ms": None,
    }
    assert out["request_id"] == REQUEST_ID
    assert out["trace_id"] in caplog.text
