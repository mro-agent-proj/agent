"""business-mcp principal: claims of the delegated token for the current call (set by BearerAuthMiddleware)."""

from contextvars import ContextVar

from ..common.auth import BearerAuthMiddleware
from ..common.errors import ToolFailure

AUDIENCE = "mro-business"
principal: ContextVar[dict | None] = ContextVar("business_principal", default=None)


def middleware(secret):
    return (BearerAuthMiddleware, {"secret": secret, "audience": AUDIENCE, "principal": principal, "realm": "mro-business"})


def authorize(request_id):
    """The token is issued for one request; tools may only read that request."""
    claims = principal.get()
    if not claims or str(request_id) != claims["request_id"]:
        raise ToolFailure("FORBIDDEN", "Request is not authorized")
    return claims
