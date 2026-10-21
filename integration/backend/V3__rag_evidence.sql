-- Generated from frozen migrations/versions/0001_rag.py. Backend owns shared DB migration.
-- Apply after existing V1/V2 through Flyway; do not also run Alembic on the same schema.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE SCHEMA IF NOT EXISTS rag;

CREATE TABLE public.knowledge_releases (
	id UUID NOT NULL,
	release_key TEXT NOT NULL,
	source_snapshot_id TEXT NOT NULL,
	package_version TEXT NOT NULL,
	manifest_sha256 TEXT NOT NULL,
	transform_version TEXT NOT NULL,
	codebook_snapshot_id TEXT NOT NULL,
	source_manifest JSONB NOT NULL,
	package_manifest JSONB NOT NULL,
	state TEXT NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	verified_at TIMESTAMP WITH TIME ZONE,
	CONSTRAINT pk_knowledge_releases PRIMARY KEY (id),
	CONSTRAINT uq_release_content_transform UNIQUE (manifest_sha256, transform_version),
	CONSTRAINT release_state CHECK (state IN ('LOADING','READY','FAILED','RETIRED')),
	CONSTRAINT uq_knowledge_releases_release_key UNIQUE (release_key)
);

CREATE TABLE public.jasc_codes (
	id UUID NOT NULL,
	release_id UUID NOT NULL,
	code TEXT NOT NULL,
	title TEXT NOT NULL,
	title_status TEXT NOT NULL,
	definition TEXT,
	codebook_version TEXT NOT NULL,
	source_url TEXT NOT NULL,
	definition_source_url TEXT,
	definition_source_file TEXT,
	title_source_file TEXT NOT NULL,
	source_payload JSONB NOT NULL,
	search_document TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', title || ' ' || coalesce(definition, ''))) STORED,
	CONSTRAINT pk_jasc_codes PRIMARY KEY (id),
	CONSTRAINT uq_jasc_codes_release_id UNIQUE (release_id, code),
	CONSTRAINT jasc_code CHECK (code ~ '^[0-9]{4}$'),
	CONSTRAINT fk_jasc_codes_release_id_knowledge_releases FOREIGN KEY(release_id) REFERENCES public.knowledge_releases (id)
);

CREATE TABLE public.knowledge_indexes (
	id UUID NOT NULL,
	release_id UUID NOT NULL,
	index_key TEXT NOT NULL,
	dataset TEXT NOT NULL,
	method TEXT NOT NULL,
	state TEXT NOT NULL,
	config_hash TEXT NOT NULL,
	config JSONB NOT NULL,
	embedding_model_version TEXT,
	embedding_dimension INTEGER,
	storage_kind TEXT NOT NULL,
	storage_locator TEXT,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	verified_at TIMESTAMP WITH TIME ZONE,
	CONSTRAINT pk_knowledge_indexes PRIMARY KEY (id),
	CONSTRAINT uq_index_release UNIQUE (release_id, id),
	CONSTRAINT uq_index_config UNIQUE (release_id, dataset, method, config_hash),
	CONSTRAINT index_state CHECK (state IN ('BUILDING','READY','FAILED','RETIRED')),
	CONSTRAINT index_method CHECK ((method = 'VECTOR' AND embedding_model_version IS NOT NULL AND embedding_dimension > 0 AND storage_kind = 'PGVECTOR') OR (method IN ('EXACT','LEXICAL') AND embedding_model_version IS NULL AND embedding_dimension IS NULL AND storage_kind = 'POSTGRES_RELATIONAL')),
	CONSTRAINT fk_knowledge_indexes_release_id_knowledge_releases FOREIGN KEY(release_id) REFERENCES public.knowledge_releases (id),
	CONSTRAINT uq_knowledge_indexes_index_key UNIQUE (index_key)
);

CREATE TABLE public.rag_request_contexts (
	run_id TEXT NOT NULL,
	request_id TEXT NOT NULL,
	context_version INTEGER NOT NULL,
	subject_id TEXT NOT NULL,
	tenant_id TEXT NOT NULL,
	operator_code TEXT,
	model_key TEXT,
	release_id UUID NOT NULL,
	index_ids JSONB NOT NULL,
	mapping_revision TEXT NOT NULL,
	locked_filters JSONB NOT NULL,
	active BOOLEAN NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT pk_rag_request_contexts PRIMARY KEY (run_id),
	CONSTRAINT context_version CHECK (context_version >= 1),
	CONSTRAINT fk_rag_request_contexts_release_id_knowledge_releases FOREIGN KEY(release_id) REFERENCES public.knowledge_releases (id)
);

CREATE TABLE public.sdr_cases (
	id UUID NOT NULL,
	release_id UUID NOT NULL,
	case_id TEXT NOT NULL,
	operator_control_number TEXT,
	operator_code TEXT NOT NULL,
	submitter_code TEXT,
	make_raw TEXT NOT NULL,
	model_raw TEXT NOT NULL,
	model_key TEXT NOT NULL,
	difficulty_date DATE NOT NULL,
	submission_date DATE NOT NULL,
	submission_at TIMESTAMP WITH TIME ZONE NOT NULL,
	submission_date_raw TEXT NOT NULL,
	reported_jasc TEXT NOT NULL,
	narrative_raw TEXT NOT NULL,
	part JSONB NOT NULL,
	component JSONB NOT NULL,
	reported_action_candidates JSONB NOT NULL,
	reported_outcome_candidates JSONB NOT NULL,
	evidence_group_id TEXT NOT NULL,
	split_group_id TEXT NOT NULL,
	split TEXT NOT NULL,
	search_eligible BOOLEAN NOT NULL,
	source_version TEXT NOT NULL,
	source_file TEXT NOT NULL,
	source_record_no INTEGER NOT NULL,
	raw_record_sha256 TEXT NOT NULL,
	raw_record JSONB NOT NULL,
	source_payload JSONB NOT NULL,
	search_document TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', narrative_raw)) STORED,
	CONSTRAINT pk_sdr_cases PRIMARY KEY (id),
	CONSTRAINT uq_sdr_cases_release_id UNIQUE (release_id, case_id),
	CONSTRAINT uq_sdr_release_id UNIQUE (release_id, id),
	CONSTRAINT fk_sdr_cases_release_id_jasc_codes FOREIGN KEY(release_id, reported_jasc) REFERENCES public.jasc_codes (release_id, code),
	CONSTRAINT sdr_corpus CHECK (split = 'corpus' AND search_eligible),
	CONSTRAINT sdr_scope CHECK (source_record_no > 0 AND operator_code <> '' AND model_key <> ''),
	CONSTRAINT fk_sdr_cases_release_id_knowledge_releases FOREIGN KEY(release_id) REFERENCES public.knowledge_releases (id)
);

CREATE TABLE public.amm_chunks (
	id UUID NOT NULL,
	release_id UUID NOT NULL,
	chunk_id TEXT NOT NULL,
	sdr_case_uuid UUID NOT NULL,
	source_version TEXT NOT NULL,
	text TEXT NOT NULL,
	context_symptom TEXT,
	start_offset INTEGER NOT NULL,
	end_offset INTEGER NOT NULL,
	embedding_text TEXT NOT NULL,
	content_hash TEXT NOT NULL,
	source_payload JSONB NOT NULL,
	CONSTRAINT pk_amm_chunks PRIMARY KEY (id),
	CONSTRAINT uq_amm_chunks_release_id UNIQUE (release_id, chunk_id),
	CONSTRAINT uq_chunk_release UNIQUE (release_id, id),
	CONSTRAINT uq_chunk_parent UNIQUE (release_id, sdr_case_uuid, id),
	CONSTRAINT fk_amm_chunks_release_id_sdr_cases FOREIGN KEY(release_id, sdr_case_uuid) REFERENCES public.sdr_cases (release_id, id),
	CONSTRAINT chunk_offsets CHECK (0 <= start_offset AND start_offset < end_offset),
	CONSTRAINT fk_amm_chunks_release_id_knowledge_releases FOREIGN KEY(release_id) REFERENCES public.knowledge_releases (id)
);

CREATE TABLE public.amm_references (
	id UUID NOT NULL,
	release_id UUID NOT NULL,
	distilled_record_id TEXT NOT NULL,
	sdr_case_uuid UUID NOT NULL,
	source_version TEXT NOT NULL,
	cited_amm_ref_raw TEXT NOT NULL,
	cited_amm_ref_normalized TEXT NOT NULL,
	reference_level TEXT NOT NULL,
	reference_start INTEGER NOT NULL,
	reference_end INTEGER NOT NULL,
	quote TEXT NOT NULL,
	quote_start INTEGER NOT NULL,
	quote_end INTEGER NOT NULL,
	symptom TEXT,
	reported_action TEXT,
	action_start INTEGER,
	action_end INTEGER,
	outcome TEXT,
	relationship_status TEXT NOT NULL,
	extraction_status TEXT NOT NULL,
	semantic_review_status TEXT NOT NULL,
	applicability_status TEXT NOT NULL,
	extraction_version TEXT NOT NULL,
	source_payload JSONB NOT NULL,
	CONSTRAINT pk_amm_references PRIMARY KEY (id),
	CONSTRAINT uq_amm_references_release_id UNIQUE (release_id, distilled_record_id),
	CONSTRAINT uq_amm_parent UNIQUE (release_id, sdr_case_uuid, id),
	CONSTRAINT fk_amm_references_release_id_sdr_cases FOREIGN KEY(release_id, sdr_case_uuid) REFERENCES public.sdr_cases (release_id, id),
	CONSTRAINT amm_offsets CHECK (0 <= quote_start AND quote_start <= reference_start AND reference_start < reference_end AND reference_end <= quote_end),
	CONSTRAINT amm_action CHECK ((reported_action IS NULL AND action_start IS NULL AND action_end IS NULL) OR (reported_action IS NOT NULL AND action_start IS NOT NULL AND action_end IS NOT NULL AND 0 <= action_start AND action_start < action_end)),
	CONSTRAINT amm_level CHECK (reference_level IN ('chapter_section','subsection','task_format')),
	CONSTRAINT amm_relationship CHECK (relationship_status IN ('explicit','ambiguous','unlinked')),
	CONSTRAINT amm_unverified CHECK (applicability_status = 'not_verified' AND semantic_review_status = 'not_reviewed'),
	CONSTRAINT fk_amm_references_release_id_knowledge_releases FOREIGN KEY(release_id) REFERENCES public.knowledge_releases (id)
);

CREATE TABLE public.amm_chunk_references (
	release_id UUID NOT NULL,
	sdr_case_uuid UUID NOT NULL,
	chunk_uuid UUID NOT NULL,
	reference_uuid UUID NOT NULL,
	CONSTRAINT pk_amm_chunk_references PRIMARY KEY (release_id, chunk_uuid, reference_uuid),
	CONSTRAINT fk_amm_chunk_references_release_id_amm_chunks FOREIGN KEY(release_id, sdr_case_uuid, chunk_uuid) REFERENCES public.amm_chunks (release_id, sdr_case_uuid, id),
	CONSTRAINT fk_amm_chunk_references_release_id_amm_references FOREIGN KEY(release_id, sdr_case_uuid, reference_uuid) REFERENCES public.amm_references (release_id, sdr_case_uuid, id)
);

CREATE TABLE public.evidence (
	id UUID NOT NULL,
	release_id UUID NOT NULL,
	evidence_id TEXT NOT NULL,
	source_type TEXT NOT NULL,
	sdr_case_uuid UUID NOT NULL,
	amm_reference_uuid UUID,
	source_id TEXT NOT NULL,
	source_version TEXT NOT NULL,
	locator JSONB NOT NULL,
	quote TEXT NOT NULL,
	CONSTRAINT pk_evidence PRIMARY KEY (id),
	CONSTRAINT uq_evidence_release_id UNIQUE (release_id, evidence_id),
	CONSTRAINT fk_evidence_release_id_sdr_cases FOREIGN KEY(release_id, sdr_case_uuid) REFERENCES public.sdr_cases (release_id, id),
	CONSTRAINT fk_evidence_release_id_amm_references FOREIGN KEY(release_id, sdr_case_uuid, amm_reference_uuid) REFERENCES public.amm_references (release_id, sdr_case_uuid, id),
	CONSTRAINT evidence_type CHECK ((source_type = 'sdr' AND amm_reference_uuid IS NULL) OR (source_type = 'derived_amm_reference' AND amm_reference_uuid IS NOT NULL)),
	CONSTRAINT fk_evidence_release_id_knowledge_releases FOREIGN KEY(release_id) REFERENCES public.knowledge_releases (id)
);

CREATE TABLE rag.amm_embeddings (
	id UUID NOT NULL,
	release_id UUID NOT NULL,
	index_uuid UUID NOT NULL,
	chunk_uuid UUID NOT NULL,
	content_hash TEXT NOT NULL,
	embedding_model_version TEXT NOT NULL,
	dimension INTEGER NOT NULL,
	embedding VECTOR(384) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT pk_amm_embeddings PRIMARY KEY (id),
	CONSTRAINT uq_amm_embeddings_index_uuid UNIQUE (index_uuid, chunk_uuid),
	CONSTRAINT fk_amm_embeddings_release_id_amm_chunks FOREIGN KEY(release_id, chunk_uuid) REFERENCES public.amm_chunks (release_id, id),
	CONSTRAINT fk_amm_embeddings_release_id_knowledge_indexes FOREIGN KEY(release_id, index_uuid) REFERENCES public.knowledge_indexes (release_id, id),
	CONSTRAINT embedding_dimension_norm CHECK (dimension = 384 AND vector_norm(embedding) > 0),
	CONSTRAINT fk_amm_embeddings_release_id_knowledge_releases FOREIGN KEY(release_id) REFERENCES public.knowledge_releases (id)
);

CREATE INDEX ix_jasc_search ON public.jasc_codes USING gin (search_document);

CREATE INDEX ix_sdr_jasc_scope ON public.sdr_cases (release_id, operator_code, model_key, reported_jasc);

CREATE INDEX ix_sdr_scope ON public.sdr_cases (release_id, operator_code, model_key, difficulty_date);

CREATE INDEX ix_sdr_search ON public.sdr_cases USING gin (search_document);

CREATE INDEX ix_chunk_parent ON public.amm_chunks (release_id, sdr_case_uuid);

CREATE INDEX ix_amm_number ON public.amm_references (release_id, cited_amm_ref_normalized);

CREATE INDEX ix_amm_parent ON public.amm_references (release_id, sdr_case_uuid);

CREATE INDEX ix_link_reference ON public.amm_chunk_references (release_id, reference_uuid);

CREATE UNIQUE INDEX uq_evidence_amm ON public.evidence (release_id, amm_reference_uuid) WHERE source_type = 'derived_amm_reference';

CREATE UNIQUE INDEX uq_evidence_sdr ON public.evidence (release_id, sdr_case_uuid) WHERE source_type = 'sdr';
