"""Streamable HTTP MCP, three read-only business tools on /mcp (team v0.3: no auth, AI server only)."""

import json
import logging
import os
from contextlib import asynccontextmanager

from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent, Tool
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from .contracts import TOOLS
from .service import BusinessService

DEFAULT_HOSTS = "127.0.0.1:*,localhost:*,testserver"


def create_app(database_url=None, source_version=None):
    database = create_async_engine(database_url or os.environ["BUSINESS_DATABASE_URL"], pool_pre_ping=True)
    service = BusinessService(
        database, source_version or os.environ.get("BUSINESS_SOURCE_VERSION", "backend-seed")
    )
    mcp = Server("mro-business")

    @mcp.list_tools()
    async def list_tools():
        return [Tool(**definition) for definition in TOOLS.values()]

    @mcp.call_tool(validate_input=False)
    async def call_tool(name, arguments):
        if name not in TOOLS:
            return CallToolResult(isError=True, content=[TextContent(type="text", text="Unknown tool")])
        result = await service.invoke(name, arguments or {})
        return CallToolResult(
            isError=result["status"] == "ERROR",
            structuredContent=result,
            content=[TextContent(type="text", text=json.dumps(result, ensure_ascii=False))],
        )

    # Docker clients send the service name as Host (e.g. business-mcp:8101); list it in BUSINESS_ALLOWED_HOSTS.
    hosts = os.environ.get("BUSINESS_ALLOWED_HOSTS", DEFAULT_HOSTS).split(",")
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
        # Refuse to start with the Spring owner account or any role that can write.
        async with database.connect() as conn:
            role = (
                await conn.execute(
                    text(
                        "SELECT rolsuper, rolcreaterole, rolcreatedb FROM pg_roles WHERE rolname = current_user"
                    )
                )
            ).one()
            if any(role):
                raise RuntimeError("business-mcp must use a restricted SELECT-only database role")
            writable = await conn.scalar(
                text(
                    "SELECT bool_or(has_table_privilege(current_user, c.oid, 'INSERT,UPDATE,DELETE,TRUNCATE')) "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND c.relkind IN ('r','p')"
                )
            )
            if writable:
                raise RuntimeError("business-mcp database role has write privileges")
        async with manager.run():
            yield
        await database.dispose()

    async def mcp_endpoint(scope, receive, send):
        await manager.handle_request(scope, receive, send)

    app = Starlette(routes=[Route("/health", health), Mount("/mcp", app=mcp_endpoint)], lifespan=lifespan)

    # Starlette Mount redirects the bare prefix; rewrite internally rather than require clients to redirect POST.
    async def exact_path(scope, receive, send):
        if scope["type"] == "http" and scope["path"] == "/mcp":
            scope = dict(scope, path="/mcp/", raw_path=b"/mcp/")
        await app(scope, receive, send)

    return exact_path


def main():
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    uvicorn.run(
        create_app(),
        host=os.environ.get("BUSINESS_HOST", "127.0.0.1"),
        port=int(os.environ.get("BUSINESS_PORT", "8101")),
    )
