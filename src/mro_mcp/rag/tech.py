"""R04 Airbus Safety First tables and model-family applicability.

The tables come from Backend Flyway V5/V8 (integration/backend/V5, V8), not from the local
Alembic chain, so they live in their own MetaData and never enter mro_rag.db migrations.
"""

import hashlib
import re

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, Integer, MetaData, Table, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR

metadata = MetaData()

documents = Table(
    "tech_documents",
    metadata,
    Column("document_id", Text, primary_key=True),
    Column("title", Text),
    Column("url", Text),
    Column("published", Text),
    Column("category", Text),
    Column("ata_chapters", ARRAY(Text)),
    Column("departments", ARRAY(Text)),
    Column("applicability_status", Text),
    Column("applicability_basis", Text),
    Column("configuration_conditions", JSONB),
    Column("source_version", Text),
    Column("pdf_sha256", Text),
    Column("body_text", Text),
    schema="public",
)

chunks = Table(
    "tech_chunks",
    metadata,
    Column("chunk_id", Text, primary_key=True),
    Column("document_id", Text),
    Column("evidence_id", Text),
    Column("evidence_group_id", Text),
    Column("section", Text),
    Column("text", Text),
    Column("start_offset", Integer),
    Column("end_offset", Integer),
    Column("applicability_status", Text),
    Column("applicability_basis", Text),
    Column("configuration_conditions", JSONB),
    Column("embedding_text", Text),
    Column("content_hash", Text),
    Column("search_document", TSVECTOR),
    schema="public",
)

embeddings = Table(
    "tech_embeddings",
    metadata,
    Column("chunk_id", Text, primary_key=True),
    Column("content_hash", Text),
    Column("embedding_model_version", Text),
    Column("embedding", Vector(1024)),  # OpenAI text-embedding-3-large@1024 since Backend V8
    schema="rag",
)

# V5: only maintenance articles are searched by default (flight_ops / engineering are crew or design topics).
SEARCH_CATEGORY = "maintenance"

# Articles name aircraft in prose ("A320 Family", "A330/A340", "all Airbus aircraft"). A model key such as
# A320214 belongs to a family; an article applies when it names that family or speaks for the whole fleet.
FAMILY_PATTERNS = {
    "A320": r"\bA ?3(18|19|20|21)(?!\d)|single[- ]aisle",
    "A330": r"\bA ?330(?!\d)",
    "A340": r"\bA ?340(?!\d)",
    "A350": r"\bA ?350(?!\d)",
    "A380": r"\bA ?380(?!\d)",
    "A300": r"\bA ?300(?!\d)",
    "A310": r"\bA ?310(?!\d)",
}
FLEET = re.compile(r"\b(all|every) airbus\b|\bairbus fleet\b|\bwhole airbus\b", re.I)


def family(model_key):
    """A320214 -> A320, A321271N -> A320, A330243 -> A330. None when the key is not an Airbus family."""
    key = (model_key or "").upper()
    if re.match(r"A3(18|19|20|21)", key):
        return "A320"
    return next((name for name in FAMILY_PATTERNS if name != "A320" and key.startswith(name)), None)


def applies(model_key, *texts):
    """True: names the request's family or the whole fleet. False: names only other families.
    None: names no aircraft type, so applicability is unknown."""
    text = " ".join(t for t in texts if t)
    own = family(model_key)
    if own and re.search(FAMILY_PATTERNS[own], text, re.I):
        return True
    if FLEET.search(text):
        return True
    if any(re.search(pattern, text, re.I) for pattern in FAMILY_PATTERNS.values()):
        return False
    return None


def snapshot_id(versions, model_version):
    """One id that changes when any article or the embedding model changes (pinned like a RAG release)."""
    digest = hashlib.sha256(("|".join(versions) + "#" + model_version).encode()).hexdigest()[:16]
    return f"TECH-{digest}"
