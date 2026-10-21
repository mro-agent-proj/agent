"""Business errors raised by tools; McpServer turns them into spec-shaped responses."""

from .models import ErrorInfo, ResponseBase

# Envelope fields a failure may carry when they were already resolved (e.g. scope before NEEDS_INPUT).
CARRIED_FIELDS = {"applied_scope", "snapshot_ids", "counts", "warnings"}


class ToolFailure(Exception):
    """A tool outcome the caller can act on. Never carries DB or stack details."""

    def __init__(self, code, message, *, status="ERROR", missing=None, retryable=False, details=None):
        super().__init__(message)
        self.code, self.message = code, message
        self.status, self.missing, self.retryable = status, list(missing or []), retryable
        unknown = set(details or {}) - CARRIED_FIELDS
        if unknown:
            raise ValueError(f"ToolFailure cannot carry {sorted(unknown)}")
        self.details = dict(details or {})


def failure_response(response_cls: type[ResponseBase], failure: ToolFailure, arguments=None):
    arguments = arguments or {}
    request_id = arguments.get("request_id")
    context_version = arguments.get("context_version")
    response = response_cls(
        status=failure.status,
        request_id=request_id if isinstance(request_id, str) else None,
        context_version=context_version if type(context_version) is int and context_version > 0 else None,
        missing_fields=failure.missing,
        error=ErrorInfo(code=failure.code, message=failure.message, retryable=failure.retryable),
        **failure.details,
    )
    # Items/result are never returned with a failure.
    response.counts.returned_count = 0
    return response
