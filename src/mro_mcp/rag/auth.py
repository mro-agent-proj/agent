"""rag-mcp principal: claims of the delegated token for the current call (set by BearerAuthMiddleware)."""

from contextvars import ContextVar

from mro_rag.context import AUDIENCE

from ..common.auth import BearerAuthMiddleware

principal: ContextVar[dict | None] = ContextVar("rag_principal", default=None)


def middleware(secret):
    return (BearerAuthMiddleware, {"secret": secret, "audience": AUDIENCE, "principal": principal, "realm": "mro-rag"})
