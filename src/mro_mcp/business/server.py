"""business-mcp entry point. Team v0.3: port 8101, mro_business_reader, called only by the AI server with a delegated token."""

import logging
import os

from ..common.app import McpServer
from ..common.auth import check_secret
from ..common.db import ReadOnlyDatabase
from . import auth, tools

DEFAULT_HOSTS = "127.0.0.1:*,localhost:*,testserver"


def create_server(database_url=None, source_version=None, jwt_secret=None):
    jwt_secret = check_secret("BUSINESS_JWT_SECRET", jwt_secret or os.environ.get("BUSINESS_JWT_SECRET"))
    mcp = McpServer(
        "mro-business",
        database=ReadOnlyDatabase(database_url or os.environ["BUSINESS_DATABASE_URL"]),
        host=os.environ.get("BUSINESS_HOST", "127.0.0.1"),
        port=int(os.environ.get("BUSINESS_PORT", "8101")),
        # Docker clients send the service name as Host (e.g. business-mcp:8101).
        allowed_hosts=os.environ.get("BUSINESS_ALLOWED_HOSTS", DEFAULT_HOSTS).split(","),
        instructions="접수 건, 같은 기체의 수리이력, 정비부서를 조회하는 읽기 전용 도구입니다.",
        http_middleware=[auth.middleware(jwt_secret)],
    )
    tools.register(mcp, source_version or os.environ.get("BUSINESS_SOURCE_VERSION", "backend-seed"))
    return mcp


def create_app():
    """ASGI factory for a standalone MCP process; the AI FastAPI app remains separate."""
    return create_server().streamable_http_app()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    create_server().run(transport="streamable-http")
