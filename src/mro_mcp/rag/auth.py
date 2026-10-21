"""Delegated bearer token from the backend; claims pin each call to one request run."""

from contextvars import ContextVar

import jwt
from starlette.responses import JSONResponse

from mro_rag.context import verify_token

principal: ContextVar[dict | None] = ContextVar("rag_principal", default=None)


class BearerAuthMiddleware:
    """Rejects /mcp without a valid token; /health stays open for container health checks."""

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
