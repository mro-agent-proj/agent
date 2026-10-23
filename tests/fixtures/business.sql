-- Synthetic fixture ONLY for the isolated mro_test_business database after Backend Flyway.
DO $$ BEGIN
  IF current_database() <> 'mro_test_business' THEN
    RAISE EXCEPTION 'Refusing to seed a non-test database';
  END IF;
END $$;
-- V4 creates NOLOGIN roles; CI enables this dedicated reader, never grants owner access.
ALTER ROLE mro_business_reader LOGIN PASSWORD 'business-ci-reader-only';
INSERT INTO airlines VALUES ('KAL', 'Synthetic airline');
INSERT INTO departments (code, name, ata_chapters) VALUES
  ('HYD', 'Synthetic hydraulics and gear', '29,32'), ('AVI', 'Synthetic avionics', '23,34');
INSERT INTO aircraft (tail_number, aircraft_model, airline_code) VALUES
  ('TEST001', 'A320-214', 'KAL'), ('TEST002', 'A320-214', 'KAL');
INSERT INTO users (id, email, name, password_hash, role, airline_code) VALUES
  ('00000000-0000-4000-8000-000000000001', 'synthetic@example.test', 'Synthetic reporter', 'not-a-login-hash', 'REPORTER', 'KAL');
INSERT INTO repair_requests (id, reporter_id, tail_number, aircraft_model, airline_code, symptom_text, status, created_at, updated_at) VALUES
  ('2203f693-f8b5-463b-bf56-b93584ef15a8', '00000000-0000-4000-8000-000000000001',
   'TEST001', 'A320-214', 'KAL', 'SYNTHETIC wheel vibration', 'CLASSIFYING', '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z');
INSERT INTO maintenance_history (id, tail_number, reported_at, symptom_text, ata_chapter, jasc_code, department_code) VALUES
  ('MH-CI-001', 'TEST001', '2026-09-01T00:00:00Z', 'SYNTHETIC wheel vibration', '32', '3230', 'HYD'),
  ('MH-CI-002', 'TEST001', '2026-09-02T00:00:00Z', 'SYNTHETIC wheel vibration', '32', '3230', 'HYD'),
  ('MH-CI-003', 'TEST001', '2026-09-03T00:00:00Z', 'SYNTHETIC wheel vibration', '32', '3230', 'HYD'),
  ('MH-CI-OTHER', 'TEST002', '2026-09-04T00:00:00Z', 'SYNTHETIC other aircraft', '32', '3230', 'HYD'),
  ('MH-CI-FUTURE', 'TEST001', '2026-10-02T00:00:00Z', 'SYNTHETIC later history', '32', '3230', 'HYD');
INSERT INTO knowledge_releases (id, release_key, source_snapshot_id, package_version, manifest_sha256,
  transform_version, codebook_snapshot_id, source_manifest, package_manifest, state, created_at) VALUES
  ('00000000-0000-4000-8000-000000000002', 'business-ci', 'synthetic', 'synthetic', 'synthetic',
   'synthetic', 'synthetic', '{}', '{}', 'READY', now());
INSERT INTO rag_request_contexts (run_id, request_id, context_version, subject_id, tenant_id, model_key,
  release_id, index_ids, mapping_revision, locked_filters, active, expires_at) VALUES
  ('business-ci-run', '2203f693-f8b5-463b-bf56-b93584ef15a8', 1, 'reporter', 'KAL', 'A320214',
   '00000000-0000-4000-8000-000000000002', '[]', 'synthetic', '{}', true, now() + interval '1 hour');
