import asyncio
import os

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from mro_rag.db import metadata


def migrate(connection):
    context.configure(connection=connection, target_metadata=metadata, include_schemas=True)
    with context.begin_transaction():
        context.run_migrations()


async def online():
    engine = create_async_engine(os.environ["DATABASE_URL"], poolclass=NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(migrate)
    await engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("Use online migrations against the explicitly configured DATABASE_URL")
asyncio.run(online())
