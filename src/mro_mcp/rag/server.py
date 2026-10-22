"""rag-mcp entry point. Team v0.3: port 8102, mro_reader, called only by the AI server with a delegated token."""

import logging
import os

from ..common.app import McpServer
from ..common.db import ReadOnlyDatabase
from . import auth, tools
from .sdr_vectors import OpenAIQueryEncoder
from .search import RagSearch

DEFAULT_HOSTS = "127.0.0.1:*,localhost:*,testserver"


def create_server(database_url=None, jwt_secret=None, cursor_secret=None, query_encoder=None):
    jwt_secret = jwt_secret or os.environ["RAG_JWT_SECRET"]
    cursor_secret = cursor_secret or os.environ["RAG_CURSOR_SECRET"]
    if min(len(jwt_secret), len(cursor_secret)) < 32 or jwt_secret == cursor_secret:
        raise ValueError("Use distinct JWT/cursor secrets with at least 32 characters each")
    database = ReadOnlyDatabase(database_url or os.environ["RAG_DATABASE_URL"], schemas=("public", "rag"))
    if query_encoder is None and os.environ.get("RAG_OPENAI_API_KEY"):
        # R02/R04 semantic search: queries use the model the documents were embedded with (Backend V7/V8).
        query_encoder = OpenAIQueryEncoder(os.environ["RAG_OPENAI_API_KEY"])
    search = RagSearch(database, cursor_secret, query_encoder)
    mcp = McpServer(
        "mro-rag",
        database=database,
        host=os.environ.get("RAG_HOST", "127.0.0.1"),
        port=int(os.environ.get("RAG_PORT", "8102")),
        # Docker clients send the service name as Host (e.g. rag-mcp:8102).
        allowed_hosts=os.environ.get("RAG_ALLOWED_HOSTS", DEFAULT_HOSTS).split(","),
        instructions="JASC 코드표, SDR 사례, SDR에 인용된 AMM 참조, Airbus 공개 기술자료와 근거 원문을 조회하는 읽기 전용 도구입니다.",
        http_middleware=[auth.middleware(jwt_secret)],
    )
    tools.register(mcp, search)
    return mcp


def create_app():
    """ASGI factory (uvicorn --factory)."""
    return create_server().streamable_http_app()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    create_server().run(transport="streamable-http")
