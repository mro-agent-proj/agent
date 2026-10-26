"""Prometheus scrape: /metrics is open like /health and counts tool calls without request data."""

import httpx

from mro_mcp.business.server import create_server
from mro_mcp.common import metrics


async def test_metrics_endpoint_is_public_and_counts_tool_calls():
    server = create_server(
        database_url="postgresql+asyncpg://unused@127.0.0.1:1/unused", jwt_secret="business-test-secret-" * 2
    )
    metrics.observe(
        server.name,
        "search_departments",
        {"status": "OK", "counts": {"returned_count": 3}, "warnings": ["VECTOR_UNAVAILABLE: x", "no code here"]},
        0.05,
    )
    app = server.streamable_http_app()  # ASGITransport does not run the lifespan, so no DB is needed
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        scraped = await client.get("/metrics")
        assert scraped.status_code == 200
        text = scraped.text
        assert 'mro_mcp_tool_calls_total{error="",server="mro-business",status="OK",tool="search_departments"}' in text
        assert 'code="VECTOR_UNAVAILABLE"' in text and 'code="OTHER"' not in text
        assert "mro_mcp_tool_call_duration_seconds_bucket" in text
        assert (await client.post("/mcp", json={})).status_code == 401  # tools still need the token
