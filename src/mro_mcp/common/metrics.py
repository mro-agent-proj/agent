"""Prometheus metrics for both MCP servers (scraped at /metrics, no token, like /health).

Labels carry only tool names, status codes and warning codes, never arguments or returned data.
"""

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.responses import Response

TOOL_CALLS = Counter(
    "mro_mcp_tool_calls_total",
    "MCP tool calls by business status and error code",
    ["server", "tool", "status", "error"],
)
TOOL_SECONDS = Histogram(
    "mro_mcp_tool_call_duration_seconds",
    "MCP tool call latency, including the database query",
    ["server", "tool"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
TOOL_RETURNED = Histogram(
    "mro_mcp_tool_returned_items",
    "Items returned per call",
    ["server", "tool"],
    buckets=(0, 1, 2, 5, 10, 20, 50, 100),
)
TOOL_WARNINGS = Counter(
    "mro_mcp_tool_warnings_total",
    "Warnings attached to responses, by code (e.g. VECTOR_UNAVAILABLE, SDR_NOT_IN_SCOPE)",
    ["server", "tool", "code"],
)


def observe(server, tool, payload, seconds):
    error = (payload.get("error") or {}).get("code") or ""
    TOOL_CALLS.labels(server, tool, payload.get("status") or "UNKNOWN", error).inc()
    TOOL_SECONDS.labels(server, tool).observe(seconds)
    TOOL_RETURNED.labels(server, tool).observe((payload.get("counts") or {}).get("returned_count") or 0)
    for warning in payload.get("warnings") or []:
        code = warning.split(":", 1)[0].strip()
        # Only CODE_LIKE prefixes count: free-text warnings are fixed usage notes (and would
        # explode label cardinality), not degradations worth alerting on.
        if code.replace("_", "").isalnum() and code.isupper():
            TOOL_WARNINGS.labels(server, tool, code).inc()


async def metrics_endpoint(request):
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
