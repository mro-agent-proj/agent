# syntax=docker/dockerfile:1
# RAG MCP runs separately from the FastAPI AI server. No local model: semantic search embeds the query with
# OpenAI (RAG_OPENAI_API_KEY), the same model the documents were embedded with (Backend V7/V8 dumps).
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_DEV=1
COPY pyproject.toml uv.lock .python-version ./
# The uv cache lives in a BuildKit cache mount, not in an image layer (otherwise every wheel is stored twice).
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen

ENV PATH="/app/.venv/bin:$PATH" \
    RAG_HOST=0.0.0.0 \
    RAG_PORT=8102
EXPOSE 8102

HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8102/health/ready', timeout=2)"
USER 65534:65534
CMD ["mro-rag-mcp"]
