-- 출처: 경님 R04 기술자료 전달본 mro-tech-handoff-20261021-v1/V5__rag_tech_docs.sql. 수정하지 않고 그대로 반영.
-- 데이터(Safety First 61편)는 이 마이그레이션 뒤에 scripts/tech-restore.sh로 복원합니다.
-- R04 search_technical_docs: Airbus Safety First 기술자료 (경)
-- 세환님 V3 테이블과 분리합니다. 데이터는 덤프로 복원하며, 아래 세 테이블이 모두 비어 있을 때만 복원합니다.
-- 조각 위치(start/end_offset)는 tech_documents.body_text 기준이고, R05가 근거 원문을 다시 확인할 때 씁니다.

CREATE TABLE public.tech_documents (
    document_id              TEXT NOT NULL,              -- DOC-<slug>
    title                    TEXT NOT NULL,
    url                      TEXT NOT NULL,              -- 원문 PDF
    published                TEXT NOT NULL,              -- 발행 호 (예: February 2009)
    category                 TEXT NOT NULL,              -- maintenance만 기본 검색 대상
    ata_chapters             TEXT[] NOT NULL,            -- 사람이 붙인 ATA. 정비 일반 글은 빈 배열
    departments              TEXT[] NOT NULL,            -- 규약 부서 매핑으로 계산
    applicability_status     TEXT NOT NULL,
    applicability_basis      TEXT,
    configuration_conditions JSONB NOT NULL,
    source_version           TEXT NOT NULL,
    pdf_sha256               TEXT NOT NULL,
    body_text                TEXT NOT NULL,
    CONSTRAINT pk_tech_documents PRIMARY KEY (document_id),
    CONSTRAINT tech_document_category CHECK (category IN ('maintenance', 'flight_ops', 'engineering')),
    CONSTRAINT tech_document_applicability CHECK (applicability_status IN ('supported', 'conditional', 'unknown'))
);

CREATE TABLE public.tech_chunks (
    chunk_id                 TEXT NOT NULL,              -- DOC-<slug>-07
    document_id              TEXT NOT NULL,
    evidence_id              TEXT NOT NULL,              -- Evidence.ref_id에 쓰는 값
    evidence_group_id        TEXT NOT NULL,              -- 같은 글의 조각은 근거 1건으로 셈
    section                  TEXT NOT NULL,
    text                     TEXT NOT NULL,
    start_offset             INTEGER NOT NULL,
    end_offset               INTEGER NOT NULL,
    applicability_status     TEXT NOT NULL,
    applicability_basis      TEXT,
    configuration_conditions JSONB NOT NULL,
    embedding_text           TEXT NOT NULL,              -- "제목 > 섹션\n본문"
    content_hash             TEXT NOT NULL,
    search_document          TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', embedding_text)) STORED,
    CONSTRAINT pk_tech_chunks PRIMARY KEY (chunk_id),
    CONSTRAINT uq_tech_chunks_evidence_id UNIQUE (evidence_id),
    CONSTRAINT fk_tech_chunks_document_id FOREIGN KEY (document_id) REFERENCES public.tech_documents (document_id),
    CONSTRAINT tech_chunk_evidence_id CHECK (evidence_id ~ '^EV-DOC-[0-9a-f]{20}$'),
    CONSTRAINT tech_chunk_offsets CHECK (0 <= start_offset AND start_offset < end_offset),
    CONSTRAINT tech_chunk_applicability CHECK (applicability_status IN ('supported', 'conditional', 'unknown'))
);

CREATE TABLE rag.tech_embeddings (
    chunk_id                 TEXT NOT NULL,
    content_hash             TEXT NOT NULL,
    embedding_model_version  TEXT NOT NULL,              -- intfloat/multilingual-e5-small@<revision>
    embedding                VECTOR(384) NOT NULL,
    CONSTRAINT pk_tech_embeddings PRIMARY KEY (chunk_id),
    CONSTRAINT fk_tech_embeddings_chunk_id FOREIGN KEY (chunk_id) REFERENCES public.tech_chunks (chunk_id),
    CONSTRAINT tech_embedding_norm CHECK (vector_norm(embedding) > 0)
);

CREATE INDEX ix_tech_chunks_document ON public.tech_chunks (document_id);
CREATE INDEX ix_tech_chunks_search ON public.tech_chunks USING gin (search_document);

-- rag-mcp (mro_reader)만 읽습니다. V4와 같은 방식
GRANT SELECT ON public.tech_documents, public.tech_chunks, rag.tech_embeddings TO mro_reader;
