-- AMM 의미 검색 데이터 제거, 기술자료(R04) 임베딩을 OpenAI(1024차원)로 교체. 전달본 sdr-jasc-amm_v2·airbus-safety-first_v2와 짝입니다.
-- 아래 DDL은 두 전달본에 들어 있는 V8__drop_amm_semantic_openai_tech.sql과 같습니다(이 출처 주석 2줄 제외).
-- 1) AMM은 판단 근거가 아니라 배정 후 참고 정보다. 근거로 쓴 SDR의 AMM 번호는 amm_references에서 바로 조회하므로
--    R03 의미 검색용 조각·연결·임베딩(로컬 e5 모델)은 더 쓰지 않는다. SDR 의미 검색은 rag.sdr_embeddings(V7)가 맡는다.
DROP TABLE rag.amm_embeddings;
DROP TABLE public.amm_chunk_references;
DROP TABLE public.amm_chunks;
DELETE FROM public.knowledge_indexes WHERE dataset = 'derived_amm' AND method = 'VECTOR';

-- 2) 기술자료 임베딩: 로컬 e5(384차원) → OpenAI text-embedding-3-large(1024차원). R02와 같은 질의 임베딩을 쓴다.
DROP TABLE rag.tech_embeddings;
CREATE TABLE rag.tech_embeddings (
    chunk_id                 TEXT NOT NULL,
    content_hash             TEXT NOT NULL,              -- sha256(tech_chunks.embedding_text)
    embedding_model_version  TEXT NOT NULL,              -- openai:text-embedding-3-large@1024
    embedding                VECTOR(1024) NOT NULL,
    CONSTRAINT pk_tech_embeddings PRIMARY KEY (chunk_id),
    CONSTRAINT fk_tech_embeddings_chunk_id FOREIGN KEY (chunk_id) REFERENCES public.tech_chunks (chunk_id),
    CONSTRAINT tech_embedding_norm CHECK (vector_norm(embedding) > 0)
);
GRANT SELECT ON rag.tech_embeddings TO mro_reader;
