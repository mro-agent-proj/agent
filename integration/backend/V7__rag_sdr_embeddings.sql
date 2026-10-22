-- R02 하이브리드 검색용 SDR 원문 임베딩. agent 저장소 scripts/embed_sdr_openai.py로 만든 전달본을 scripts/sdr-embed-restore.sh로 복원합니다.
-- 아래 DDL은 전달본 V7__rag_sdr_embeddings.sql과 같습니다(이 출처 주석 2줄 제외).
-- SDR 원문(narrative_raw) 임베딩: OpenAI text-embedding-3-large, 1024차원(앞쪽 잘라 쓰기), 코사인 거리.
-- 검색할 때 질의도 같은 모델·차원으로 임베딩해야 합니다. 접수 범위(기종) 안에서 정확 검색하므로 근사 색인은 두지 않습니다.
CREATE TABLE rag.sdr_embeddings (
    release_id              UUID NOT NULL,
    sdr_case_uuid           UUID NOT NULL,
    content_hash            TEXT NOT NULL,              -- sha256(narrative_raw)
    embedding_model_version TEXT NOT NULL,              -- openai:text-embedding-3-large@1024
    embedding               VECTOR(1024) NOT NULL,
    CONSTRAINT pk_sdr_embeddings PRIMARY KEY (release_id, sdr_case_uuid),
    CONSTRAINT fk_sdr_embeddings_sdr FOREIGN KEY (release_id, sdr_case_uuid)
        REFERENCES public.sdr_cases (release_id, id),
    CONSTRAINT sdr_embedding_norm CHECK (vector_norm(embedding) > 0)
);

-- rag-mcp (mro_reader)만 읽습니다. V4·V5와 같은 방식
GRANT SELECT ON rag.sdr_embeddings TO mro_reader;
