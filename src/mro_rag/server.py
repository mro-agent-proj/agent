"""Authenticated Streamable HTTP MCP, four read-only tools on /mcp."""

import json
import logging
import os
from contextlib import asynccontextmanager
from contextvars import ContextVar

import jwt
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent, Tool
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from .context import verify_token
from .contracts import TOOLS
from .db import engine
from .service import RetrievalService

principal = ContextVar("rag_principal", default=None)


class AuthMiddleware:
    def __init__(self, app, secret):
        self.app, self.secret = app, secret

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] == "/health":
            return await self.app(scope, receive, send)
        headers = {k.lower(): v for k, v in scope["headers"]}
        try:
            value = headers.get(b"authorization", b"").decode()
            if not value.startswith("Bearer "):
                raise jwt.InvalidTokenError()
            claims = verify_token(self.secret, value[7:])
        except (jwt.InvalidTokenError, UnicodeDecodeError):
            return await JSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="mro-rag"'},
            )(scope, receive, send)
        token = principal.set(claims)
        try:
            await self.app(scope, receive, send)
        finally:
            principal.reset(token)


def create_app(database_url=None, jwt_secret=None, cursor_secret=None, encoder=None):
    jwt_secret = jwt_secret or os.environ["RAG_JWT_SECRET"]
    cursor_secret = cursor_secret or os.environ["RAG_CURSOR_SECRET"]
    if min(len(jwt_secret), len(cursor_secret)) < 32 or jwt_secret == cursor_secret:
        raise ValueError("Use distinct JWT/cursor secrets with at least 32 characters each")
    database = engine(database_url or os.environ["RAG_DATABASE_URL"])
    service = RetrievalService(database, cursor_secret, encoder)
    mcp = Server("mro-rag")

    @mcp.list_tools()
    async def list_tools():
        return [Tool(**definition) for definition in TOOLS.values()]

    @mcp.call_tool(validate_input=False)
    async def call_tool(name, arguments):
        if name not in TOOLS:
            return CallToolResult(isError=True, content=[TextContent(type="text", text="Unknown tool")])
        result = await service.invoke(name, arguments, principal.get())
        return CallToolResult(
            isError=result["status"] == "ERROR",
            structuredContent=result,
            content=[TextContent(type="text", text=json.dumps(result, ensure_ascii=False))],
        )

    hosts = os.environ.get("RAG_ALLOWED_HOSTS", "127.0.0.1:*,localhost:*,testserver").split(",")
    manager = StreamableHTTPSessionManager(
        mcp,
        json_response=True,
        stateless=True,
        security_settings=TransportSecuritySettings(allowed_hosts=hosts),
    )

    async def health(request):
        try:
            async with database.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return JSONResponse({"status": "ok"})
        except (SQLAlchemyError, OSError):
            return JSONResponse({"status": "unavailable"}, status_code=503)

    @asynccontextmanager
    async def lifespan(app):
        # Deployment errors must be visible before accepting requests.
        async with database.connect() as conn:
            role = (
                await conn.execute(
                    text(
                        "SELECT rolsuper, rolcreaterole, rolcreatedb FROM pg_roles WHERE rolname = current_user"
                    )
                )
            ).one()
            if any(role):
                raise RuntimeError("RAG server must use a restricted SELECT-only database role")
            writable = await conn.scalar(
                text(
                    "SELECT bool_or(has_table_privilege(current_user, c.oid, 'INSERT,UPDATE,DELETE,TRUNCATE')) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname IN ('public','rag') AND c.relkind IN ('r','p')"
                )
            )
            if writable:
                raise RuntimeError("RAG database role has write privileges")
        async with manager.run():
            yield
        await database.dispose()

    async def mcp_endpoint(scope, receive, send):
        await manager.handle_request(scope, receive, send)

    # Mount at root so /mcp is not redirected to /mcp/.
    app = Starlette(routes=[Route("/health", health), Mount("/mcp", app=mcp_endpoint)], lifespan=lifespan)

    # Starlette Mount redirects the bare prefix; rewrite internally rather than require clients to redirect POST.
    class ExactPath:
        async def __call__(self, scope, receive, send):
            if scope["type"] == "http" and scope["path"] == "/mcp":
                scope = dict(scope, path="/mcp/", raw_path=b"/mcp/")
            await app(scope, receive, send)

    return AuthMiddleware(ExactPath(), jwt_secret)


def main():
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(
        create_app(),
        host=os.environ.get("RAG_HOST", "127.0.0.1"),
        port=int(os.environ.get("RAG_PORT", "8102")),
    )
