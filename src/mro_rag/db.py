"""Relational schema. Migrations, not the running service, create these tables."""

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.ext.asyncio import create_async_engine

metadata = MetaData(
    schema="public",
    naming_convention={
        "pk": "pk_%(table_name)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "ix": "ix_%(table_name)s_%(column_0_name)s",
    },
)


def col(name, typ=Text, nullable=False, **kwargs):
    return Column(name, typ, nullable=nullable, **kwargs)


def identity():
    return col("id", UUID(as_uuid=True), primary_key=True)


def release():
    return Column(
        "release_id", UUID(as_uuid=True), ForeignKey("public.knowledge_releases.id"), nullable=False
    )


releases = Table(
    "knowledge_releases",
    metadata,
    identity(),
    col("release_key", unique=True),
    col("source_snapshot_id"),
    col("package_version"),
    col("manifest_sha256"),
    col("transform_version"),
    col("codebook_snapshot_id"),
    col("source_manifest", JSONB),
    col("package_manifest", JSONB),
    col("state"),
    col("created_at", DateTime(timezone=True)),
    col("verified_at", DateTime(timezone=True), True),
    UniqueConstraint("manifest_sha256", "transform_version", name="uq_release_content_transform"),
    CheckConstraint("state IN ('LOADING','READY','FAILED','RETIRED')", name="release_state"),
)
jasc = Table(
    "jasc_codes",
    metadata,
    identity(),
    release(),
    col("code"),
    col("title"),
    col("title_status"),
    col("definition", nullable=True),
    col("codebook_version"),
    col("source_url"),
    col("definition_source_url", nullable=True),
    col("definition_source_file", nullable=True),
    col("title_source_file"),
    col("source_payload", JSONB),
    Column(
        "search_document",
        TSVECTOR,
        Computed("to_tsvector('english', title || ' ' || coalesce(definition, ''))", persisted=True),
    ),
    UniqueConstraint("release_id", "code"),
    CheckConstraint("code ~ '^[0-9]{4}$'", name="jasc_code"),
)
sdr = Table(
    "sdr_cases",
    metadata,
    identity(),
    release(),
    col("case_id"),
    col("operator_control_number", nullable=True),
    col("operator_code"),
    col("submitter_code", nullable=True),
    col("make_raw"),
    col("model_raw"),
    col("model_key"),
    col("difficulty_date", Date),
    col("submission_date", Date),
    col("submission_at", DateTime(timezone=True)),
    col("submission_date_raw"),
    col("reported_jasc"),
    col("narrative_raw"),
    col("part", JSONB),
    col("component", JSONB),
    col("reported_action_candidates", JSONB),
    col("reported_outcome_candidates", JSONB),
    col("evidence_group_id"),
    col("split_group_id"),
    col("split"),
    col("search_eligible", Boolean),
    col("source_version"),
    col("source_file"),
    col("source_record_no", Integer),
    col("raw_record_sha256"),
    col("raw_record", JSONB),
    col("source_payload", JSONB),
    Column("search_document", TSVECTOR, Computed("to_tsvector('english', narrative_raw)", persisted=True)),
    UniqueConstraint("release_id", "case_id"),
    UniqueConstraint("release_id", "id", name="uq_sdr_release_id"),
    ForeignKeyConstraint(
        ["release_id", "reported_jasc"], ["public.jasc_codes.release_id", "public.jasc_codes.code"]
    ),
    CheckConstraint("split = 'corpus' AND search_eligible", name="sdr_corpus"),
    CheckConstraint("source_record_no > 0 AND operator_code <> '' AND model_key <> ''", name="sdr_scope"),
)
amm = Table(
    "amm_references",
    metadata,
    identity(),
    release(),
    col("distilled_record_id"),
    col("sdr_case_uuid", UUID(as_uuid=True)),
    col("source_version"),
    col("cited_amm_ref_raw"),
    col("cited_amm_ref_normalized"),
    col("reference_level"),
    col("reference_start", Integer),
    col("reference_end", Integer),
    col("quote"),
    col("quote_start", Integer),
    col("quote_end", Integer),
    col("symptom", nullable=True),
    col("reported_action", nullable=True),
    col("action_start", Integer, True),
    col("action_end", Integer, True),
    col("outcome", nullable=True),
    col("relationship_status"),
    col("extraction_status"),
    col("semantic_review_status"),
    col("applicability_status"),
    col("extraction_version"),
    col("source_payload", JSONB),
    UniqueConstraint("release_id", "distilled_record_id"),
    UniqueConstraint("release_id", "sdr_case_uuid", "id", name="uq_amm_parent"),
    ForeignKeyConstraint(
        ["release_id", "sdr_case_uuid"], ["public.sdr_cases.release_id", "public.sdr_cases.id"]
    ),
    CheckConstraint(
        "0 <= quote_start AND quote_start <= reference_start AND reference_start < reference_end AND reference_end <= quote_end",
        name="amm_offsets",
    ),
    CheckConstraint(
        "(reported_action IS NULL AND action_start IS NULL AND action_end IS NULL) OR (reported_action IS NOT NULL AND action_start IS NOT NULL AND action_end IS NOT NULL AND 0 <= action_start AND action_start < action_end)",
        name="amm_action",
    ),
    CheckConstraint("reference_level IN ('chapter_section','subsection','task_format')", name="amm_level"),
    CheckConstraint("relationship_status IN ('explicit','ambiguous','unlinked')", name="amm_relationship"),
    CheckConstraint(
        "applicability_status = 'not_verified' AND semantic_review_status = 'not_reviewed'",
        name="amm_unverified",
    ),
)
chunks = Table(
    "amm_chunks",
    metadata,
    identity(),
    release(),
    col("chunk_id"),
    col("sdr_case_uuid", UUID(as_uuid=True)),
    col("source_version"),
    col("text"),
    col("context_symptom", nullable=True),
    col("start_offset", Integer),
    col("end_offset", Integer),
    col("embedding_text"),
    col("content_hash"),
    col("source_payload", JSONB),
    UniqueConstraint("release_id", "chunk_id"),
    UniqueConstraint("release_id", "id", name="uq_chunk_release"),
    UniqueConstraint("release_id", "sdr_case_uuid", "id", name="uq_chunk_parent"),
    ForeignKeyConstraint(
        ["release_id", "sdr_case_uuid"], ["public.sdr_cases.release_id", "public.sdr_cases.id"]
    ),
    CheckConstraint("0 <= start_offset AND start_offset < end_offset", name="chunk_offsets"),
)
links = Table(
    "amm_chunk_references",
    metadata,
    col("release_id", UUID(as_uuid=True), primary_key=True),
    col("sdr_case_uuid", UUID(as_uuid=True)),
    col("chunk_uuid", UUID(as_uuid=True), primary_key=True),
    col("reference_uuid", UUID(as_uuid=True), primary_key=True),
    ForeignKeyConstraint(
        ["release_id", "sdr_case_uuid", "chunk_uuid"],
        ["public.amm_chunks.release_id", "public.amm_chunks.sdr_case_uuid", "public.amm_chunks.id"],
    ),
    ForeignKeyConstraint(
        ["release_id", "sdr_case_uuid", "reference_uuid"],
        [
            "public.amm_references.release_id",
            "public.amm_references.sdr_case_uuid",
            "public.amm_references.id",
        ],
    ),
)
evidence = Table(
    "evidence",
    metadata,
    identity(),
    release(),
    col("evidence_id"),
    col("source_type"),
    col("sdr_case_uuid", UUID(as_uuid=True)),
    col("amm_reference_uuid", UUID(as_uuid=True), True),
    col("source_id"),
    col("source_version"),
    col("locator", JSONB),
    col("quote"),
    UniqueConstraint("release_id", "evidence_id"),
    ForeignKeyConstraint(
        ["release_id", "sdr_case_uuid"], ["public.sdr_cases.release_id", "public.sdr_cases.id"]
    ),
    ForeignKeyConstraint(
        ["release_id", "sdr_case_uuid", "amm_reference_uuid"],
        [
            "public.amm_references.release_id",
            "public.amm_references.sdr_case_uuid",
            "public.amm_references.id",
        ],
    ),
    CheckConstraint(
        "(source_type = 'sdr' AND amm_reference_uuid IS NULL) OR (source_type = 'derived_amm_reference' AND amm_reference_uuid IS NOT NULL)",
        name="evidence_type",
    ),
)
indexes = Table(
    "knowledge_indexes",
    metadata,
    identity(),
    release(),
    col("index_key", unique=True),
    col("dataset"),
    col("method"),
    col("state"),
    col("config_hash"),
    col("config", JSONB),
    col("embedding_model_version", nullable=True),
    col("embedding_dimension", Integer, True),
    col("storage_kind"),
    col("storage_locator", nullable=True),
    col("created_at", DateTime(timezone=True)),
    col("verified_at", DateTime(timezone=True), True),
    UniqueConstraint("release_id", "id", name="uq_index_release"),
    UniqueConstraint("release_id", "dataset", "method", "config_hash", name="uq_index_config"),
    CheckConstraint("state IN ('BUILDING','READY','FAILED','RETIRED')", name="index_state"),
    CheckConstraint(
        "(method = 'VECTOR' AND embedding_model_version IS NOT NULL AND embedding_dimension > 0 AND storage_kind = 'PGVECTOR') OR (method IN ('EXACT','LEXICAL') AND embedding_model_version IS NULL AND embedding_dimension IS NULL AND storage_kind = 'POSTGRES_RELATIONAL')",
        name="index_method",
    ),
)
embeddings = Table(
    "amm_embeddings",
    metadata,
    identity(),
    release(),
    col("index_uuid", UUID(as_uuid=True)),
    col("chunk_uuid", UUID(as_uuid=True)),
    col("content_hash"),
    col("embedding_model_version"),
    col("dimension", Integer),
    col("embedding", Vector(384)),
    col("created_at", DateTime(timezone=True)),
    UniqueConstraint("index_uuid", "chunk_uuid"),
    ForeignKeyConstraint(
        ["release_id", "chunk_uuid"], ["public.amm_chunks.release_id", "public.amm_chunks.id"]
    ),
    ForeignKeyConstraint(
        ["release_id", "index_uuid"], ["public.knowledge_indexes.release_id", "public.knowledge_indexes.id"]
    ),
    CheckConstraint("dimension = 384 AND vector_norm(embedding) > 0", name="embedding_dimension_norm"),
    schema="rag",
)
# Backend writes this projection only after request authorization and code mapping.
# A signed run token cannot override these authoritative values or reactivate a revoked run.
contexts = Table(
    "rag_request_contexts",
    metadata,
    col("run_id", primary_key=True),
    col("request_id"),
    col("context_version", Integer),
    col("subject_id"),
    col("tenant_id"),
    col("operator_code", nullable=True),
    col("model_key", nullable=True),
    release(),
    col("index_ids", JSONB),
    col("mapping_revision"),
    col("model_scope", JSONB(none_as_null=True), nullable=True),
    col("locked_filters", JSONB),
    col("active", Boolean),
    col("expires_at", DateTime(timezone=True)),
    CheckConstraint("context_version >= 1", name="context_version"),
)

Index("ix_sdr_scope", sdr.c.release_id, sdr.c.operator_code, sdr.c.model_key, sdr.c.difficulty_date)
Index("ix_sdr_jasc_scope", sdr.c.release_id, sdr.c.operator_code, sdr.c.model_key, sdr.c.reported_jasc)
Index("ix_sdr_search", sdr.c.search_document, postgresql_using="gin")
Index("ix_jasc_search", jasc.c.search_document, postgresql_using="gin")
Index("ix_amm_number", amm.c.release_id, amm.c.cited_amm_ref_normalized)
Index("ix_amm_parent", amm.c.release_id, amm.c.sdr_case_uuid)
Index("ix_chunk_parent", chunks.c.release_id, chunks.c.sdr_case_uuid)
Index("ix_link_reference", links.c.release_id, links.c.reference_uuid)
Index(
    "uq_evidence_sdr",
    evidence.c.release_id,
    evidence.c.sdr_case_uuid,
    unique=True,
    postgresql_where=evidence.c.source_type == "sdr",
)
Index(
    "uq_evidence_amm",
    evidence.c.release_id,
    evidence.c.amm_reference_uuid,
    unique=True,
    postgresql_where=evidence.c.source_type == "derived_amm_reference",
)


def engine(url):
    return create_async_engine(url, pool_pre_ping=True)
