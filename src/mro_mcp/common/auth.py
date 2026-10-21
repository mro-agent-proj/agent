"""Delegated bearer tokens from the backend; claims pin each call to one request run.

Both MCP servers use the same middleware with their own secret, audience and principal variable:
business-mcp (aud "mro-business") and rag-mcp (aud "mro-rag"). The backend signs one token per run.
"""

from contextvars import ContextVar

import jwt
from starlette.responses import JSONResponse

from mro_rag.context import verify_token

HEALTH_PATHS = {"/health", "/health/live", "/health/ready"}


class BearerAuthMiddleware:
    """Rejects /mcp without a valid token; exact health paths stay open for probes."""

    def __init__(self, app, secret, audience, principal: ContextVar, realm):
        self.app, self.secret, self.audience, self.principal, self.realm = app, secret, audience, principal, realm

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] in HEALTH_PATHS:
            return await self.app(scope, receive, send)
        headers = {k.lower(): v for k, v in scope["headers"]}
        try:
            value = headers.get(b"authorization", b"").decode()
            if not value.startswith("Bearer "):
                raise jwt.InvalidTokenError()
            claims = verify_token(self.secret, value[7:], audience=self.audience)
        except (jwt.InvalidTokenError, UnicodeDecodeError):
            return await JSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": f'Bearer realm="{self.realm}"'},
            )(scope, receive, send)
        token = self.principal.set(claims)
        try:
            await self.app(scope, receive, send)
        finally:
            self.principal.reset(token)


def check_secret(name, secret):
    if not secret or len(secret) < 32:
        raise ValueError(f"{name} must be at least 32 characters")
    return secret
