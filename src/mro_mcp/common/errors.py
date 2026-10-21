"""Business errors raised by tools; McpServer turns them into spec-shaped responses."""

from .models import ErrorInfo, ResponseBase


class ToolFailure(Exception):
    """A tool outcome the caller can act on. Never carries DB or stack details."""

    def __init__(self, code, message, *, status="ERROR", missing=None, retryable=False):
        super().__init__(message)
        self.code, self.message = code, message
        self.status, self.missing, self.retryable = status, list(missing or []), retryable


def failure_response(response_cls: type[ResponseBase], failure: ToolFailure, request_id=None, trace_id=None):
    extra = {"trace_id": trace_id} if trace_id else {}
    return response_cls(
        status=failure.status,
        request_id=request_id,
        missing_fields=failure.missing,
        error=ErrorInfo(code=failure.code, message=failure.message, retryable=failure.retryable),
        **extra,
    )
