"""In-process MCP caller for tests and offline verification scripts (no HTTP, no token signing).

The caller sets the delegated claims that BearerAuthMiddleware would set from a real token, then calls the
tool over the MCP protocol so validation, envelopes and isError are exercised exactly as in production.
"""

from mcp.shared.memory import create_connected_server_and_client_session

from ..common.app import McpServer
from .auth import principal


class LocalCaller:
    def __init__(self, server: McpServer):
        self.server = server

    async def __aenter__(self):
        await self.server.database.startup()
        return self

    async def __aexit__(self, *exc):
        await self.server.database.shutdown()

    async def call(self, name, args, claims):
        """Returns the MCP CallToolResult."""
        token = principal.set(claims)
        try:
            async with create_connected_server_and_client_session(self.server._mcp_server) as session:
                return await session.call_tool(name, args)
        finally:
            principal.reset(token)

    async def invoke(self, name, args, claims):
        """Returns structuredContent, the shape RetrievalService.invoke used to return."""
        return (await self.call(name, args, claims)).structuredContent
