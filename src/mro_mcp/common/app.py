"""FastMCP with spec-shaped errors: every tool failure becomes a response envelope with isError."""

import json
import logging
import time
from contextlib import asynccontextmanager
from typing import get_type_hints

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import JSONResponse

from .db import ReadOnlyDatabase
from .errors import ToolFailure, failure_response
from .models import ResponseBase

LOG = logging.getLogger(__name__)


def describe_validation(error: ValidationError) -> str:
    parts = [f"{'.'.join(map(str, e['loc'])) or 'arguments'}: {e['msg']}" for e in error.errors()]
    return ("Input does not match the tool contract: " + "; ".join(parts))[:2000]


class McpServer(FastMCP):
    """Streamable HTTP, stateless, JSON responses. Tools return a ResponseBase model or raise ToolFailure."""

    def __init__(self, name, *, database: ReadOnlyDatabase, host, port, allowed_hosts, instructions=None):
        super().__init__(
            name,
            instructions=instructions,
            host=host,
            port=port,
            stateless_http=True,
            json_response=True,
            transport_security=TransportSecuritySettings(allowed_hosts=list(allowed_hosts)),
        )
        self.database = database
        self._responses: dict[str, type[ResponseBase]] = {}
        self.custom_route("/health", methods=["GET"])(self._health)

    def add_tool(self, fn, name=None, **kwargs):
        response_cls = get_type_hints(fn).get("return")
        if not (isinstance(response_cls, type) and issubclass(response_cls, ResponseBase)):
            raise TypeError(f"Tool {fn.__name__} must be annotated to return a ResponseBase model")
        self._responses[name or fn.__name__] = response_cls
        super().add_tool(fn, name=name, **kwargs)

    async def call_tool(self, name, arguments):
        response_cls = self._responses.get(name)
        if response_cls is None:
            return await super().call_tool(name, arguments)
        started = time.monotonic()
        try:
            content, structured = await super().call_tool(name, arguments)
            self._log(name, structured, started)
            return content, structured
        except ToolError as exc:
            cause = exc.__cause__
            if isinstance(cause, ToolFailure):
                failure = cause
            elif isinstance(cause, ValidationError):
                failure = ToolFailure("INVALID_ARGUMENT", describe_validation(cause))
            else:
                failure = ToolFailure(
                    "INTERNAL_ERROR", "Lookup failed; use trace_id to investigate", retryable=True
                )
        request_id = arguments.get("request_id") if isinstance(arguments.get("request_id"), str) else None
        payload = failure_response(response_cls, failure, request_id=request_id).model_dump(mode="json")
        if failure.code == "INTERNAL_ERROR":
            LOG.error("Tool failure tool=%s trace=%s", name, payload["trace_id"], exc_info=cause)
        self._log(name, payload, started)
        return CallToolResult(
            isError=failure.status == "ERROR",
            structuredContent=payload,
            content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
        )

    @staticmethod
    def _log(name, payload, started):
        LOG.info(
            "tool=%s trace=%s status=%s returned=%s error=%s ms=%d",
            name,
            payload["trace_id"],
            payload["status"],
            payload["counts"]["returned_count"],
            (payload["error"] or {}).get("code"),
            (time.monotonic() - started) * 1000,
        )

    async def _health(self, request):
        try:
            await self.database.ping()
            return JSONResponse({"status": "ok"})
        except (SQLAlchemyError, OSError, AttributeError):
            return JSONResponse({"status": "unavailable"}, status_code=503)

    def streamable_http_app(self):
        app = super().streamable_http_app()
        session_lifespan = app.router.lifespan_context

        @asynccontextmanager
        async def lifespan(starlette_app):
            # Deployment errors (wrong DB role, unreachable DB) must fail before accepting requests.
            await self.database.startup()
            try:
                async with session_lifespan(starlette_app):
                    yield
            finally:
                await self.database.shutdown()

        app.router.lifespan_context = lifespan
        return app
