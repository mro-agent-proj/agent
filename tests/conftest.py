"""Synthetic, independently specified cases. Never require the FAA source package in CI."""

import hashlib
import json
import os
from importlib.resources import files
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest
import pytest_asyncio
from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy import delete, insert, select, text

from mro_mcp.rag import tech
from mro_mcp.rag.local import LocalCaller
from mro_mcp.rag.server import create_server
from mro_rag import db
from mro_rag.context import publish_context
from mro_rag.embedding import MODEL_VERSION
from mro_rag.ingest import ingest


class FixedEncoder:
    """Deterministic vectors for SQL/ranking tests, not a semantic quality evaluation."""

    def encode(self, texts, **kwargs):
        vectors = np.zeros((len(texts), 384), dtype=np.float32)
        for i, value in enumerate(texts):
            vectors[i, 0 if "wheel" in value.lower() else 1] = 1
        return vectors


def write_package(root):
    def write_jsonl(path, rows):
        file = root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("".join(json.dumps(row) + "\n" for row in rows))

    sdr, amm, chunks, raw = [], [], [], []
    for i, operator, model, code in [
        (1, "TESTA", "A320214", "3210"),
        (2, "TESTA", "A320214", "2924"),
        (3, "OTHER", "A320214", "3210"),
    ]:
        narrative = f"CASE {i}: WHEEL VIBRATION. REPLACED WHEEL PER AMM 32-11-00. TEST OK."
        start = narrative.index("32-11-00")
        action_start, action_end = narrative.index("REPLACED"), narrative.index("AMM")
        loc = {
            "source_url": "https://example.test/synthetic",
            "file_id": "synthetic.csv",
            "record_key": str(i),
            "section": "Discrepancy",
            "start_offset": 0,
            "end_offset": len(narrative),
        }
        shared = {
            "operator_code": operator,
            "model_key": model,
            "reported_jasc": code,
            "difficulty_date": "2025-01-01",
            "evidence_group_id": f"EG-{i}",
            "split_group_id": f"SG-{i}",
            "split": "corpus",
            "search_eligible": True,
        }
        case_id, ref_id = f"SDR-TEST-{i}", f"AMM-TEST-{i}"
        s = dict(
            shared,
            case_id=case_id,
            operator_control_number=str(i),
            submitter_code=None,
            make_raw="AIRBUS",
            model_raw=model,
            submission_date="2025-01-02T01:00:00+09:00",
            narrative_raw=narrative,
            part={},
            component={"name": "WHEEL"},
            reported_action_candidates=[
                {"text": "REPLACED WHEEL", "start": action_start, "end": action_start + 14}
            ],
            reported_outcome_candidates=[],
            source_version="test-source",
            source_file="synthetic.csv",
            source_record_no=i,
            source_type="sdr",
            source_id=case_id,
            evidence_id=f"EV-SDR-TEST-{i}",
            locator=loc,
            quote=narrative,
        )
        a = dict(
            shared,
            distilled_record_id=ref_id,
            source_case_id=case_id,
            source_type="derived_amm_reference",
            source_id=ref_id,
            source_version="test-source:test-extract",
            cited_amm_ref_raw="32-11-00",
            cited_amm_ref_normalized="32-11-00",
            reference_level="subsection",
            reference_start=start,
            reference_end=start + 8,
            quote=narrative,
            quote_start=0,
            quote_end=len(narrative),
            symptom="WHEEL VIBRATION",
            reported_action=narrative[action_start:action_end].strip(),
            action_start=action_start,
            action_end=action_end,
            outcome="TEST OK",
            relationship_status="explicit",
            extraction_status="rule_extracted_unreviewed",
            semantic_review_status="not_reviewed",
            applicability_status="not_verified",
            extraction_version="test-extract",
            evidence_id=f"EV-AMM-TEST-{i}",
            locator=loc,
            parent_source_ids=[case_id],
        )
        c = dict(
            shared,
            chunk_id=f"CHUNK-{i}",
            source_case_id=case_id,
            source_version="test-source",
            text=narrative,
            context_symptom=None,
            start_offset=0,
            end_offset=len(narrative),
            embedding_text=narrative,
            content_hash=hashlib.sha256(narrative.encode()).hexdigest(),
            linked_reference_ids=[ref_id],
        )
        original = {"Discrepancy": narrative, **{f"OriginalField{j}": "" for j in range(75)}}
        raw.append(
            {
                "case_id": case_id,
                "raw": original,
                "source_sha256": "test-source",
                "raw_record_sha256": hashlib.sha256(json.dumps(original).encode()).hexdigest(),
            }
        )
        sdr.append(s)
        amm.append(a)
        chunks.append(c)
    write_jsonl("runtime/sdr_cases.jsonl", sdr)
    write_jsonl("runtime/amm_references.jsonl", amm)
    write_jsonl("rag/amm_chunks.jsonl", chunks)
    write_jsonl("canonical/raw_airbus_records.jsonl", raw)
    write_jsonl("evaluation/holdout_sdr_cases.jsonl", [])
    write_jsonl(
        "reference/jasc_codes.jsonl",
        [
            {
                "code": code,
                "title": title,
                "title_status": status,
                "definition": None,
                "codebook_version": "test-v1",
                "source_url": "https://example.test/codebook",
                "definition_source_url": None,
                "definition_source_file": None,
                "title_source_file": "synthetic.pdf",
            }
            for code, title, status in [
                ("3210", "Landing gear wheel", "official_pdf"),
                ("2924", "JASC 2924", "code_only_no_title_found"),
            ]
        ],
    )
    (root / "provenance").mkdir()
    (root / "provenance/source_manifest.json").write_text("{}")
    manifest = {
        "version": "synthetic-v1",
        "snapshot_id": "TEST-SNAPSHOT",
        "files": [
            {"path": str(f.relative_to(root)), "sha256": hashlib.sha256(f.read_bytes()).hexdigest()}
            for f in sorted(root.rglob("*"))
            if f.is_file()
        ],
    }
    (root / "package_manifest.json").write_text(json.dumps(manifest))


@pytest.fixture(scope="session")
def package(tmp_path_factory):
    root = tmp_path_factory.mktemp("source")
    write_package(root)
    return root


@pytest_asyncio.fixture
async def setup_db(package):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL required for PostgreSQL integration tests")
    if not url.rsplit("/", 1)[-1].startswith("mro_test"):
        pytest.fail("Tests require a separate mro_test database")
    owner = db.engine(url)
    result = await ingest(owner, package)
    async with owner.connect() as conn:
        release_id = (
            await conn.execute(
                select(db.releases.c.id).where(db.releases.c.release_key == result["release_key"])
            )
        ).scalar_one()
    reader = db.engine(os.environ["TEST_RAG_DATABASE_URL"])
    server = create_server(
        database_url=os.environ["TEST_RAG_DATABASE_URL"],
        jwt_secret="jwt-test-secret-" * 3,
        cursor_secret="cursor-test-secret-" * 3,
        encoder=FixedEncoder(),
    )
    async with ContractCheckingCaller(server) as service:
        yield owner, reader, service, release_id, result["release_key"]
    await reader.dispose()
    await owner.dispose()


class ContractCheckingCaller(LocalCaller):
    """Every response must still satisfy the spec v1.0 JSON Schema the Pydantic models replaced."""

    async def invoke(self, name, args, claims):
        result = await super().invoke(name, args, claims)
        if name in LEGACY_OUTPUTS:  # local extensions are checked by their Pydantic models only
            LEGACY_OUTPUTS[name].validate(result)
        return result


def legacy_outputs():
    catalog = json.loads(files("mro_rag").joinpath("tool_contracts.json").read_text())
    return {
        t["mcp"]["name"]: Draft202012Validator(t["mcp"]["outputSchema"], format_checker=FormatChecker())
        for t in catalog["tools"]
        if t["id"] in {"R01", "R02", "R03", "R04", "R05"}
    }


LEGACY_OUTPUTS = legacy_outputs()


async def make_context(owner, release_id, *, operator="TESTA", model="A320214", filters=None):
    run = "test-" + uuid4().hex
    async with owner.begin() as conn:
        await publish_context(
            conn,
            run_id=run,
            request_id=run,
            context_version=1,
            subject_id="alice",
            tenant_id="tenant-a",
            operator_code=operator,
            model_key=model,
            release_id=release_id,
            mapping_revision="synthetic-v1",
            locked_filters=filters,
        )
    claims = {"run_id": run, "request_id": run, "context_version": 1, "sub": "alice", "tenant_id": "tenant-a"}
    return claims, {"request_id": run, "context_version": 1}


TECH_DDL = Path(__file__).resolve().parent.parent / "integration/backend/V5__rag_tech_docs.sql"

# Synthetic articles (not Airbus text): one per applicability case R04 must tell apart.
TECH_ARTICLES = [
    ("DOC-T-A320", "maintenance", "A320 wheel and brake vibration", "supported",
     "This applies to the A320 Family.", [], "Wheel vibration during taxi is often traced to brake wear."),
    ("DOC-T-FLEET", "maintenance", "Radio altimeter checks", "conditional",
     "All Airbus aircraft are fitted with two radio altimeters.", ["Only aircraft with the new antenna"],
     "Erroneous radio altimeter values after water ingress in the antenna."),
    ("DOC-T-A330", "maintenance", "A330 wheel bleed leak", "supported",
     "Applies to the A330.", [], "Wheel area bleed leak detection on the A330."),
    ("DOC-T-NOTYPE", "maintenance", "Corrosion awareness", "unknown",
     None, [], "Wheel well corrosion found during inspection."),
    ("DOC-T-OPS", "flight_ops", "A320 wheel landing technique", "supported",
     "A320 Family crews.", [], "Wheel landing technique in gusty wind."),
]


async def seed_tech(owner):
    """Creates the Backend V5 tables once (the local Alembic chain has no R04) and reloads the articles."""
    async with owner.begin() as conn:
        if await conn.scalar(text("SELECT to_regclass('public.tech_documents')")) is None:
            for statement in TECH_DDL.read_text().split(";\n"):
                if any(line.strip() and not line.strip().startswith("--") for line in statement.splitlines()):
                    await conn.exec_driver_sql(statement)
        await conn.execute(delete(tech.embeddings))
        await conn.execute(delete(tech.chunks))
        await conn.execute(delete(tech.documents))
        encoder = FixedEncoder()
        for doc, category, title, status, basis, conditions, body in TECH_ARTICLES:
            await conn.execute(
                insert(tech.documents).values(
                    document_id=doc, title=title, url=f"https://example.test/{doc}.pdf", published="June 2007",
                    category=category, ata_chapters=[], departments=[], applicability_status=status,
                    applicability_basis=basis, configuration_conditions=conditions, source_version=f"tech-test:{doc}",
                    pdf_sha256="0" * 64, body_text=body,
                )
            )
            embedding_text = f"{title} > Summary\n{body}"
            await conn.execute(
                insert(tech.chunks).values(
                    chunk_id=f"{doc}-01", document_id=doc,
                    evidence_id="EV-DOC-" + hashlib.md5(doc.encode()).hexdigest()[:20], evidence_group_id=doc,
                    section="Summary", text=body, start_offset=0, end_offset=len(body), applicability_status=status,
                    applicability_basis=basis, configuration_conditions=conditions, embedding_text=embedding_text,
                    content_hash=hashlib.sha256(embedding_text.encode()).hexdigest(),
                )
            )
            await conn.execute(
                insert(tech.embeddings).values(
                    chunk_id=f"{doc}-01", content_hash="test", embedding_model_version=MODEL_VERSION,
                    embedding=encoder.encode([embedding_text])[0].tolist(),
                )
            )
