"""R02 semantic half: SDR narrative embeddings (Backend V7) and the query encoder that must match them.

The table comes from Backend Flyway V7 (integration/backend/V7__rag_sdr_embeddings.sql), not the local
Alembic chain, so it has its own MetaData. Documents were embedded by scripts/embed_sdr_openai.py.
"""

import httpx
from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, MetaData, Table, Text
from sqlalchemy.dialects.postgresql import UUID

MODEL, DIMENSIONS = "text-embedding-3-large", 1024
MODEL_VERSION = f"openai:{MODEL}@{DIMENSIONS}"

metadata = MetaData()

embeddings = Table(
    "sdr_embeddings",
    metadata,
    Column("release_id", UUID(as_uuid=True), primary_key=True),
    Column("sdr_case_uuid", UUID(as_uuid=True), primary_key=True),
    Column("content_hash", Text),
    Column("embedding_model_version", Text),
    Column("embedding", Vector(DIMENSIONS)),
    schema="rag",
)


class OpenAIQueryEncoder:
    """Embeds one search query per call. Must be the model and dimensions the documents used."""

    version = MODEL_VERSION

    def __init__(self, api_key, timeout=5.0):
        self.api_key, self.timeout = api_key, timeout

    async def encode(self, text):
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                "https://api.openai.com/v1/embeddings",
                json={"model": MODEL, "dimensions": DIMENSIONS, "input": [text[:8000]]},
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            response.raise_for_status()
            return response.json()["data"][0]["embedding"]
