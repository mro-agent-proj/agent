# business-mcp (B01~B03). Build from the repository root:
#   docker build -f docker/business-mcp.Dockerfile -t mro-business-mcp .
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_DEV=1

# Dependencies first so source edits reuse this layer.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-install-project

COPY src ./src
RUN uv sync --frozen

ENV PATH="/app/.venv/bin:$PATH" \
    BUSINESS_HOST=0.0.0.0 \
    BUSINESS_PORT=8101
EXPOSE 8101

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8101/health', timeout=2)"

USER nobody
CMD ["mro-business-mcp"]
