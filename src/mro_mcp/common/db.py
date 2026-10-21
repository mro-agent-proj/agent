"""SELECT-only database access. The server refuses to start with a role that could write."""

from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

INSUFFICIENT_PRIVILEGE = "42501"


def is_permission_denied(exc):
    return getattr(getattr(exc, "orig", None), "sqlstate", None) == INSUFFICIENT_PRIVILEGE


class ReadOnlyDatabase:
    def __init__(self, url: str, schemas=("public",)):
        self.url, self.schemas = url, tuple(schemas)
        self.engine: AsyncEngine | None = None

    async def startup(self):
        self.engine = create_async_engine(self.url, pool_pre_ping=True)
        async with self.engine.connect() as conn:
            role = (
                await conn.execute(
                    text(
                        "SELECT rolsuper, rolcreaterole, rolcreatedb FROM pg_roles WHERE rolname = current_user"
                    )
                )
            ).one()
            if any(role):
                raise RuntimeError("MCP server must use a restricted SELECT-only database role")
            writable = await conn.scalar(
                text(
                    "SELECT bool_or(has_table_privilege(current_user, c.oid, 'INSERT,UPDATE,DELETE,TRUNCATE')) "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = ANY(:schemas) AND c.relkind IN ('r','p')"
                ),
                {"schemas": list(self.schemas)},
            )
            if writable:
                raise RuntimeError("MCP database role has write privileges")

    async def shutdown(self):
        if self.engine is not None:
            await self.engine.dispose()

    @asynccontextmanager
    async def read(self):
        """One REPEATABLE READ, READ ONLY transaction per tool call."""
        async with self.engine.connect() as conn, conn.begin():
            await conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            await conn.execute(text("SET LOCAL statement_timeout = '8s'"))
            yield conn

    async def ping(self):
        async with self.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
