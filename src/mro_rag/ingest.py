"""Offline, atomic, idempotent ingestion. Never exposed as an MCP tool."""

import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import insert, select, text, update

from . import db

TRANSFORM_VERSION = "postgres-v1"


def digest(value):
    return hashlib.sha256(value).hexdigest()


def json_hash(value):
    return digest(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def read_rows(path):
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load_package(path):
    """Validate before insertion; no source mutation or evaluation rows in runtime."""
    path = Path(path).resolve()
    manifest_bytes = (path / "package_manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    for entry in manifest["files"]:
        file = (path / entry["path"]).resolve()
        require(file.is_relative_to(path), "Manifest path outside source package")
        require(digest(file.read_bytes()) == entry["sha256"], f"Manifest hash mismatch: {entry['path']}")
    datasets = {
        name: list(read_rows(path / file))
        for name, file in {
            "jasc": "reference/jasc_codes.jsonl",
            "sdr": "runtime/sdr_cases.jsonl",
            "amm": "runtime/amm_references.jsonl",
            "chunks": "rag/amm_chunks.jsonl",
        }.items()
    }
    for name, key in [
        ("jasc", "code"),
        ("sdr", "case_id"),
        ("amm", "distilled_record_id"),
        ("chunks", "chunk_id"),
    ]:
        require(len({r[key] for r in datasets[name]}) == len(datasets[name]), f"Duplicate {name} ID")
    sdr = {r["case_id"]: r for r in datasets["sdr"]}
    amm = {r["distilled_record_id"]: r for r in datasets["amm"]}
    codes = {r["code"] for r in datasets["jasc"]}
    holdout = list(read_rows(path / "evaluation/holdout_sdr_cases.jsonl"))
    require(not (set(sdr) & {r["case_id"] for r in holdout}), "Holdout case leakage")
    require(
        not ({r["split_group_id"] for r in sdr.values()} & {r["split_group_id"] for r in holdout}),
        "Holdout group leakage",
    )
    raw = {
        r["case_id"]: r for r in read_rows(path / "canonical/raw_airbus_records.jsonl") if r["case_id"] in sdr
    }
    for r in sdr.values():
        require(
            r["split"] == "corpus" and r["search_eligible"] and r["reported_jasc"] in codes,
            "Invalid runtime scope/JASC",
        )
        original = raw[r["case_id"]]
        require(
            len(original["raw"]) == 76 and original["raw"]["Discrepancy"] == r["narrative_raw"],
            "Raw source mismatch",
        )
        require(original["source_sha256"] == r["source_version"], "Raw version mismatch")
        require(r["source_type"] == "sdr" and r["source_id"] == r["case_id"], "SDR source identity mismatch")
    for r in datasets["amm"]:
        parent = sdr[r["source_case_id"]]
        for key in (
            "operator_code",
            "model_key",
            "reported_jasc",
            "difficulty_date",
            "evidence_group_id",
            "split_group_id",
            "split",
        ):
            require(r[key] == parent[key], f"AMM parent mismatch: {key}")
        narrative = parent["narrative_raw"]
        require(
            r["search_eligible"]
            and r["source_type"] == "derived_amm_reference"
            and r["source_id"] == r["distilled_record_id"],
            "AMM source identity mismatch",
        )
        require(r["parent_source_ids"] == [r["source_case_id"]], "AMM parent source IDs mismatch")
        require(
            r["source_version"] == parent["source_version"] + ":" + r["extraction_version"],
            "AMM version mismatch",
        )
        require(narrative[r["quote_start"] : r["quote_end"]] == r["quote"], "AMM quote mismatch")
        require(
            narrative[r["reference_start"] : r["reference_end"]] == r["cited_amm_ref_raw"],
            "AMM number mismatch",
        )
        if r["reported_action"] is not None:
            require(
                narrative[r["action_start"] : r["action_end"]].strip() == r["reported_action"],
                "AMM action mismatch",
            )
    for r in datasets["chunks"]:
        parent = sdr[r["source_case_id"]]
        require(
            parent["narrative_raw"][r["start_offset"] : r["end_offset"]] == r["text"], "Chunk quote mismatch"
        )
        require(r["source_version"] == parent["source_version"], "Chunk version mismatch")
        require(digest(r["embedding_text"].encode()) == r["content_hash"], "Chunk hash mismatch")
        require(
            r["linked_reference_ids"]
            and len(set(r["linked_reference_ids"])) == len(r["linked_reference_ids"]),
            "Empty/duplicate chunk links",
        )
        require(
            all(amm[key]["source_case_id"] == r["source_case_id"] for key in r["linked_reference_ids"]),
            "Cross-case chunk link",
        )
    all_evidence = datasets["sdr"] + datasets["amm"]
    require(len({r["evidence_id"] for r in all_evidence}) == len(all_evidence), "Duplicate evidence ID")
    for r in all_evidence:
        narrative = (
            r["narrative_raw"] if r["source_type"] == "sdr" else sdr[r["source_case_id"]]["narrative_raw"]
        )
        loc = r["locator"]
        require(0 <= loc["start_offset"] < loc["end_offset"] <= len(narrative), "Evidence range invalid")
        require(narrative[loc["start_offset"] : loc["end_offset"]] == r["quote"], "Evidence quote mismatch")
    return (
        manifest,
        digest(manifest_bytes),
        datasets,
        raw,
        json.loads((path / "provenance/source_manifest.json").read_text()),
    )


async def insert_batches(conn, table, rows):
    for offset in range(0, len(rows), 500):
        await conn.execute(insert(table), rows[offset : offset + 500])


def project(table, row):
    return {c.name: row[c.name] for c in table.columns if c.name in row and c.computed is None}


async def ingest(engine, path):
    manifest, manifest_hash, data, raw, sources = load_package(path)
    release_key = f"REL-{manifest_hash[:24]}-{TRANSFORM_VERSION}"
    now = datetime.now(UTC)
    async with engine.begin() as conn:
        await conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": release_key}
        )
        existing = (
            (await conn.execute(select(db.releases).where(db.releases.c.release_key == release_key)))
            .mappings()
            .first()
        )
        if existing:
            require(
                existing["state"] == "READY" and existing["manifest_sha256"] == manifest_hash,
                "Existing release not ready or hash differs",
            )
            return {"release_key": release_key, "release_id": str(existing["id"]), "reused": True}
        rid = uuid4()
        await conn.execute(
            insert(db.releases).values(
                id=rid,
                release_key=release_key,
                source_snapshot_id=manifest["snapshot_id"],
                package_version=manifest["version"],
                manifest_sha256=manifest_hash,
                transform_version=TRANSFORM_VERSION,
                codebook_snapshot_id="JASC-" + json_hash(data["jasc"])[:24],
                source_manifest=sources,
                package_manifest=manifest,
                state="LOADING",
                created_at=now,
            )
        )
        await insert_batches(
            conn,
            db.jasc,
            [dict(project(db.jasc, r), id=uuid4(), release_id=rid, source_payload=r) for r in data["jasc"]],
        )
        sdr_ids = {r["case_id"]: uuid4() for r in data["sdr"]}
        amm_ids = {r["distilled_record_id"]: uuid4() for r in data["amm"]}
        chunk_ids = {r["chunk_id"]: uuid4() for r in data["chunks"]}
        rows = []
        for r in data["sdr"]:
            original = raw[r["case_id"]]
            dt = datetime.fromisoformat(r["submission_date"])
            require(dt.tzinfo is not None, "Submission timestamp lacks offset")
            row = project(db.sdr, r)
            row.update(
                id=sdr_ids[r["case_id"]],
                release_id=rid,
                difficulty_date=date.fromisoformat(r["difficulty_date"]),
                submission_date=dt.date(),
                submission_at=dt,
                submission_date_raw=r["submission_date"],
                raw_record=original["raw"],
                raw_record_sha256=original["raw_record_sha256"],
                source_payload=r,
            )
            rows.append(row)
        await insert_batches(conn, db.sdr, rows)
        await insert_batches(
            conn,
            db.amm,
            [
                dict(
                    project(db.amm, r),
                    id=amm_ids[r["distilled_record_id"]],
                    release_id=rid,
                    sdr_case_uuid=sdr_ids[r["source_case_id"]],
                    source_payload=r,
                )
                for r in data["amm"]
            ],
        )
        await insert_batches(
            conn,
            db.chunks,
            [
                dict(
                    project(db.chunks, r),
                    id=chunk_ids[r["chunk_id"]],
                    release_id=rid,
                    sdr_case_uuid=sdr_ids[r["source_case_id"]],
                    source_payload=r,
                )
                for r in data["chunks"]
            ],
        )
        await insert_batches(
            conn,
            db.links,
            [
                {
                    "release_id": rid,
                    "sdr_case_uuid": sdr_ids[r["source_case_id"]],
                    "chunk_uuid": chunk_ids[r["chunk_id"]],
                    "reference_uuid": amm_ids[ref],
                }
                for r in data["chunks"]
                for ref in r["linked_reference_ids"]
            ],
        )
        await insert_batches(
            conn,
            db.evidence,
            [
                dict(
                    project(db.evidence, r),
                    id=uuid4(),
                    release_id=rid,
                    sdr_case_uuid=sdr_ids[r.get("source_case_id", r.get("case_id"))],
                    amm_reference_uuid=amm_ids.get(r.get("distilled_record_id")),
                )
                for r in data["sdr"] + data["amm"]
            ],
        )
        for dataset, method in [("jasc", "LEXICAL"), ("sdr", "LEXICAL"), ("derived_amm", "EXACT")]:
            config = {"parser": "english-websearch-v1", "dataset": dataset, "method": method}
            h = json_hash(config)
            await conn.execute(
                insert(db.indexes).values(
                    id=uuid4(),
                    release_id=rid,
                    index_key=f"IDX-{release_key}-{dataset}-{h[:12]}",
                    dataset=dataset,
                    method=method,
                    state="READY",
                    config_hash=h,
                    config=config,
                    storage_kind="POSTGRES_RELATIONAL",
                    created_at=now,
                    verified_at=now,
                )
            )
        await conn.execute(
            update(db.releases).where(db.releases.c.id == rid).values(state="READY", verified_at=now)
        )
    return {
        "release_key": release_key,
        "release_id": str(rid),
        "reused": False,
        "counts": {k: len(v) for k, v in data.items()},
    }
