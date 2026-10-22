import asyncio
import os
import socket
import subprocess
import sys

import httpx
import jwt
import pytest
from conftest import make_context
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from mro_rag.context import issue_token, verify_token

JWT_SECRET = "jwt-integration-only-" * 3
CURSOR_SECRET = "cursor-integration-only-" * 3


def test_token_expiry_audience_signature():
    args = dict(subject_id="alice", tenant_id="a", run_id="run", request_id="req", context_version=1)
    token = issue_token(JWT_SECRET, **args)
    assert verify_token(JWT_SECRET, token)["sub"] == "alice"
    with pytest.raises(jwt.InvalidTokenError):
        verify_token("different-secret-" * 3, token)
    with pytest.raises(jwt.ExpiredSignatureError):
        verify_token(JWT_SECRET, issue_token(JWT_SECRET, **args, seconds=-10))
    claims = jwt.decode(token, JWT_SECRET, algorithms=["HS256"], options={"verify_aud": False})
    claims["aud"] = "other-service"
    with pytest.raises(jwt.InvalidAudienceError):
        verify_token(JWT_SECRET, jwt.encode(claims, JWT_SECRET, algorithm="HS256"))


async def test_streamable_http_tool_contract_and_authorization(setup_db, tmp_path):
    owner, _, _, rid, _ = setup_db
    claims, args = await make_context(owner, rid)
    token = issue_token(
        JWT_SECRET,
        subject_id=claims["sub"],
        tenant_id=claims["tenant_id"],
        run_id=claims["run_id"],
        request_id=claims["request_id"],
        context_version=1,
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = dict(
        os.environ,
        RAG_DATABASE_URL=os.environ["TEST_RAG_DATABASE_URL"],
        RAG_JWT_SECRET=JWT_SECRET,
        RAG_CURSOR_SECRET=CURSOR_SECRET,
    )
    log = tmp_path / "server.log"
    with log.open("w") as stream:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "mro_mcp.rag.server:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            env=env,
            stdout=stream,
            stderr=stream,
        )
    base = f"http://127.0.0.1:{port}"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            for _ in range(100):
                try:
                    if (await client.get(base + "/health")).status_code == 200:
                        break
                except httpx.ConnectError:
                    pass
                if process.poll() is not None:
                    pytest.fail(log.read_text())
                await asyncio.sleep(0.05)
            else:
                pytest.fail("Server did not become ready: " + log.read_text())
            assert (await client.post(base + "/mcp", json={})).status_code == 401
        async with (
            httpx.AsyncClient(headers={"Authorization": "Bearer " + token}, timeout=10) as client,
            streamable_http_client(base + "/mcp", http_client=client) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            listed = await session.list_tools()
            assert len(listed.tools) == 6
            assert all(t.annotations.readOnlyHint for t in listed.tools)
            r = await session.call_tool("search_jasc_codes", dict(args, codes=["3210"]))
            assert not r.isError and r.structuredContent["status"] == "OK"
            bad = await session.call_tool(
                "search_sdr_cases", dict(args, query="wheel", operator_code="OTHER")
            )
            assert bad.isError and bad.structuredContent["error"]["code"] == "INVALID_ARGUMENT"
            wrong = await session.call_tool(
                "search_jasc_codes", dict(args, request_id="someone-else", codes=["3210"])
            )
            assert wrong.isError and wrong.structuredContent["error"]["code"] == "FORBIDDEN"
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
