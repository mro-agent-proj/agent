-- Run only as the local dev DB owner after Alembic migrations.
-- This sample password is for the loopback-only compose environment.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mro_reader') THEN
    CREATE ROLE mro_reader LOGIN PASSWORD 'local-reader-only'
      NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
  END IF;
END $$;
SELECT format('GRANT CONNECT ON DATABASE %I TO mro_reader', current_database()) \gexec
GRANT USAGE ON SCHEMA public, rag TO mro_reader;
GRANT SELECT ON public.knowledge_releases, public.knowledge_indexes,
  public.jasc_codes, public.sdr_cases, public.amm_references,
  public.amm_chunks, public.amm_chunk_references, public.evidence,
  public.rag_request_contexts, rag.amm_embeddings TO mro_reader;
ALTER ROLE mro_reader SET default_transaction_read_only = on;
