# RAG MCP runs separately from the FastAPI AI server.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_DEV=1
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --extra embedding --no-install-project
COPY src ./src
RUN uv sync --frozen --extra embedding \
    && mkdir -p /var/cache/mro/huggingface \
    && chown -R 65534:65534 /var/cache/mro

ENV PATH="/app/.venv/bin:$PATH" \
    RAG_HOST=0.0.0.0 \
    RAG_PORT=8102 \
    RAG_PRELOAD_ENCODER=true \
    HF_HOME=/var/cache/mro/huggingface \
    MRO_EMBED_DEVICE=cpu
EXPOSE 8102

# The first start downloads pinned public weights; allow time before accepting AI calls.
HEALTHCHECK --interval=10s --timeout=3s --start-period=300s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8102/health/ready', timeout=2)"
USER 65534:65534
CMD ["mro-rag-mcp"]
