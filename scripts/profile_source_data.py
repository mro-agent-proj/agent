#!/usr/bin/env python3
"""Read preserved source files; write design-time profiles, never modify a DB."""

import csv
import json
import sqlite3
from collections import Counter
from datetime import datetime
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/source/airbus-mcp-data-v1"
REPORTS = ROOT / "reports"
MAPPINGS = ROOT / "docs/mappings"


def read_jsonl(relative):
    with (SOURCE / relative).open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def write_csv(name, fields, rows):
    with (MAPPINGS / name).open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main():
    REPORTS.mkdir(exist_ok=True)
    MAPPINGS.mkdir(parents=True, exist_ok=True)
    files = {
        "sdr": "runtime/sdr_cases.jsonl",
        "amm": "runtime/amm_references.jsonl",
        "chunks": "rag/amm_chunks.jsonl",
        "jasc": "reference/jasc_codes.jsonl",
    }
    data = {name: read_jsonl(path) for name, path in files.items()}
    sdr = {row["case_id"]: row for row in data["sdr"]}
    amm = {row["distilled_record_id"]: row for row in data["amm"]}
    jasc = {row["code"] for row in data["jasc"]}
    checks = []

    def check(name, failures):
        checks.append(
            {"name": name, "passed": not failures, "failure_count": len(failures), "examples": failures[:5]}
        )

    for name, key in [
        ("sdr", "case_id"),
        ("amm", "distilled_record_id"),
        ("chunks", "chunk_id"),
        ("jasc", "code"),
    ]:
        counts = Counter(row[key] for row in data[name])
        check(name + "_unique_id", [key for key, count in counts.items() if count != 1])
    check(
        "runtime_corpus_only",
        [r["case_id"] for r in data["sdr"] if r["split"] != "corpus" or not r["search_eligible"]],
    )
    check("runtime_jasc_exists", [r["case_id"] for r in data["sdr"] if r["reported_jasc"] not in jasc])
    check(
        "amm_source_scope_and_group",
        [
            r["distilled_record_id"]
            for r in data["amm"]
            if r["source_case_id"] not in sdr
            or any(
                r[k] != sdr[r["source_case_id"]][k]
                for k in (
                    "operator_code",
                    "model_key",
                    "reported_jasc",
                    "difficulty_date",
                    "evidence_group_id",
                    "split_group_id",
                    "split",
                )
            )
        ],
    )
    check(
        "amm_reference_and_quote_offsets",
        [
            r["distilled_record_id"]
            for r in data["amm"]
            if r["source_case_id"] not in sdr
            or sdr[r["source_case_id"]]["narrative_raw"][r["reference_start"] : r["reference_end"]]
            != r["cited_amm_ref_raw"]
            or sdr[r["source_case_id"]]["narrative_raw"][r["quote_start"] : r["quote_end"]] != r["quote"]
        ],
    )
    check(
        "amm_range_constraints",
        [
            r["distilled_record_id"]
            for r in data["amm"]
            if not (0 <= r["quote_start"] <= r["reference_start"] < r["reference_end"] <= r["quote_end"])
        ],
    )
    bad_actions = []
    action_text_forms = Counter()
    for row in data["amm"]:
        action, start, end = row["reported_action"], row["action_start"], row["action_end"]
        parent = sdr.get(row["source_case_id"])
        valid = (
            start is None and end is None
            if action is None
            else (
                parent is not None
                and isinstance(start, int)
                and isinstance(end, int)
                and 0 <= start < end <= len(parent["narrative_raw"])
                # build_dataset.py stores strip() text with the original untrimmed span.
                and parent["narrative_raw"][start:end].strip() == action
            )
        )
        if not valid:
            bad_actions.append(row["distilled_record_id"])
        elif action is None:
            action_text_forms["null"] += 1
        elif parent["narrative_raw"][start:end] == action:
            action_text_forms["exact"] += 1
        else:
            action_text_forms["boundary_whitespace_only"] += 1
    check("amm_action_null_range_and_text", bad_actions)
    check(
        "chunk_source_quote_and_hash",
        [
            r["chunk_id"]
            for r in data["chunks"]
            if r["source_case_id"] not in sdr
            or sdr[r["source_case_id"]]["narrative_raw"][r["start_offset"] : r["end_offset"]] != r["text"]
            or sha256(r["embedding_text"].encode()).hexdigest() != r["content_hash"]
        ],
    )
    check(
        "chunk_reference_same_source",
        [
            r["chunk_id"]
            for r in data["chunks"]
            if not r["linked_reference_ids"]
            or any(
                ref not in amm or amm[ref]["source_case_id"] != r["source_case_id"]
                for ref in r["linked_reference_ids"]
            )
        ],
    )
    check(
        "chunk_duplicate_links",
        [
            r["chunk_id"]
            for r in data["chunks"]
            if len(r["linked_reference_ids"]) != len(set(r["linked_reference_ids"]))
        ],
    )
    check(
        "chunk_source_version_matches_parent",
        [
            r["chunk_id"]
            for r in data["chunks"]
            if r["source_case_id"] not in sdr
            or r["source_version"] != sdr[r["source_case_id"]]["source_version"]
        ],
    )
    check(
        "embeddings_not_generated",
        [
            r["chunk_id"]
            for r in data["chunks"]
            if r["embedding_status"] != "not_generated" or r["embedding"] is not None
        ],
    )
    bad_dates = []
    for row in data["sdr"]:
        try:
            assert datetime.fromisoformat(row["submission_date"]).tzinfo is not None
        except (ValueError, TypeError, AssertionError):
            bad_dates.append(row["case_id"])
    check("submission_timestamp_has_offset", bad_dates)
    holdout = read_jsonl("evaluation/holdout_sdr_cases.jsonl")
    check("holdout_ids_disjoint", sorted(set(sdr) & {r["case_id"] for r in holdout}))
    check(
        "holdout_groups_disjoint",
        sorted({r["split_group_id"] for r in data["sdr"]} & {r["split_group_id"] for r in holdout}),
    )
    with sqlite3.connect((SOURCE / "runtime.sqlite").resolve().as_uri() + "?mode=ro", uri=True) as db:
        sqlite_counts = {
            table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "sdr_cases",
                "amm_references",
                "amm_chunks",
                "jasc_codes",
                "evidence",
                "aircraft",
                "aircraft_history",
                "departments",
            )
        }
        evidence_counts = dict(db.execute("SELECT source_type, COUNT(*) FROM evidence GROUP BY source_type"))
        expected_evidence = {}
        duplicate_evidence = []
        for row in data["sdr"] + data["amm"]:
            key = row["evidence_id"]
            if key in expected_evidence:
                duplicate_evidence.append(key)
            expected_evidence[key] = row
        check("evidence_ids_unique_across_sources", duplicate_evidence)
        evidence_failures = []
        seen_evidence = set()
        fields = (
            "source_type",
            "source_id",
            "source_version",
            "evidence_group_id",
            "operator_code",
            "model_key",
        )
        for values in db.execute(
            "SELECT evidence_id, source_type, source_id, source_version, evidence_group_id, operator_code, model_key, locator_json FROM evidence"
        ):
            evidence_id, *rest = values
            seen_evidence.add(evidence_id)
            original = expected_evidence.get(evidence_id)
            if (
                original is None
                or any(value != original[field] for field, value in zip(fields, rest[:6], strict=True))
                or json.loads(rest[6]) != original["locator"]
            ):
                evidence_failures.append(evidence_id)
        evidence_failures.extend(sorted(set(expected_evidence) - seen_evidence))
        check("evidence_registry_matches_source_and_scope", evidence_failures)
    for name, table in [
        ("sdr", "sdr_cases"),
        ("amm", "amm_references"),
        ("chunks", "amm_chunks"),
        ("jasc", "jasc_codes"),
    ]:
        check(name + "_sqlite_count", [] if len(data[name]) == sqlite_counts[table] else [table])
    scope_sdr = Counter((r["operator_code"], r["model_key"]) for r in data["sdr"])
    scope_amm = Counter((r["operator_code"], r["model_key"]) for r in data["amm"])
    scope_chunks = Counter((r["operator_code"], r["model_key"]) for r in data["chunks"])
    write_csv(
        "scope-coverage.csv",
        ["operator_code", "model_key", "sdr_count", "amm_reference_count", "chunk_count"],
        [
            dict(
                operator_code=o,
                model_key=m,
                sdr_count=count,
                amm_reference_count=scope_amm[o, m],
                chunk_count=scope_chunks[o, m],
            )
            for (o, m), count in sorted(scope_sdr.items())
        ],
    )
    operators = Counter(r["operator_code"] for r in data["sdr"])
    models = Counter(r["model_key"] for r in data["sdr"])
    write_csv(
        "operator-inventory.csv",
        ["operator_code", "sdr_count", "service_airline_code", "verification_status"],
        [
            dict(operator_code=o, sdr_count=n, service_airline_code="", verification_status="UNRESOLVED")
            for o, n in sorted(operators.items())
        ],
    )
    write_csv(
        "model-inventory.csv",
        ["model_key", "sdr_count", "model_raw_values", "service_aircraft_model", "verification_status"],
        [
            dict(
                model_key=m,
                sdr_count=n,
                model_raw_values=json.dumps(
                    sorted({r["model_raw"] for r in data["sdr"] if r["model_key"] == m}), ensure_ascii=False
                ),
                service_aircraft_model="",
                verification_status="UNRESOLVED",
            )
            for m, n in sorted(models.items())
        ],
    )
    profiles = {}
    for name, rows in data.items():
        keys = sorted({k for row in rows for k in row})
        profiles[name] = {
            "path": files[name],
            "sha256": sha256((SOURCE / files[name]).read_bytes()).hexdigest(),
            "count": len(rows),
            "fields": {
                key: {
                    "missing": sum(key not in row for row in rows),
                    "null": sum(row.get(key) is None for row in rows),
                    "types": dict(Counter(type(row[key]).__name__ for row in rows if key in row)),
                }
                for key in keys
            },
        }
    manifest = json.loads((SOURCE / "package_manifest.json").read_text())
    # Original CSV columns are distinct from the derived runtime payload fields.
    raw_field_counts = {}
    raw_fields = None
    raw_schema_failures = []
    raw_join_failures = []
    raw_runtime_ids = set()
    raw_count = 0
    for line in (SOURCE / "canonical/raw_airbus_records.jsonl").open(encoding="utf-8"):
        original = json.loads(line)
        raw = original["raw"]
        raw_count += 1
        if raw_fields is None:
            raw_fields = list(raw)
        if list(raw) != raw_fields:
            raw_schema_failures.append(original["case_id"])
        for key, value in raw.items():
            raw_field_counts.setdefault(key, Counter())[value] += 1
        if original["case_id"] in sdr:
            raw_runtime_ids.add(original["case_id"])
            normalized = sdr[original["case_id"]]
            if (
                raw["Discrepancy"] != normalized["narrative_raw"]
                or original["source_sha256"] != normalized["source_version"]
            ):
                raw_join_failures.append(original["case_id"])
    check(
        "raw_76_columns_same_order", raw_schema_failures + ([] if len(raw_fields) == 76 else ["field_count"])
    )
    check("runtime_has_preserved_raw", sorted(set(sdr) - raw_runtime_ids))
    check("runtime_raw_text_and_version_match", raw_join_failures)
    write_csv(
        "raw-sdr-columns.csv",
        ["ordinal", "source_field", "nonempty_count", "distinct_nonempty_count", "examples_json"],
        [
            dict(
                ordinal=i,
                source_field=key,
                nonempty_count=sum(n for v, n in counts.items() if v not in ("", None)),
                distinct_nonempty_count=sum(v not in ("", None) for v in counts),
                examples_json=json.dumps(
                    [v for v, n in counts.most_common() if v not in ("", None)][:3], ensure_ascii=False
                ),
            )
            for i, (key, counts) in enumerate(raw_field_counts.items(), 1)
        ],
    )
    bad_hashes = [
        f["path"]
        for f in manifest["files"]
        if sha256((SOURCE / f["path"]).read_bytes()).hexdigest() != f["sha256"]
    ]
    check("preserved_package_manifest_hashes", bad_hashes)
    report = {
        "scope": "Design-time local source inspection; not PostgreSQL or retrieval-quality validation",
        "source_snapshot_id": manifest["snapshot_id"],
        "profiles": profiles,
        "raw_record_count": raw_count,
        "raw_field_count": len(raw_fields),
        "amm_action_text_forms": dict(action_text_forms),
        "raw_file_sha256": sha256((SOURCE / "canonical/raw_airbus_records.jsonl").read_bytes()).hexdigest(),
        "sqlite_counts": sqlite_counts,
        "evidence_source_counts": evidence_counts,
        "unique_operator_codes": len(operators),
        "unique_model_keys": len(models),
        "scope_pairs": len(scope_sdr),
        "amm_chunk_reference_links": sum(len(r["linked_reference_ids"]) for r in data["chunks"]),
        "holdout_sdr_count": len(holdout),
        "jasc_codebook_versions": dict(Counter(r["codebook_version"] for r in data["jasc"])),
        "jasc_unresolved_titles": [r["code"] for r in data["jasc"] if r["title_status"] != "official_pdf"],
        "literal_operator_KAL_count": operators["KAL"],
        "mapping_note": "Inventories are observations, not verified ICAO/model aliases. Re-running overwrites generated inventories; maintain approved mappings separately.",
        "checks": checks,
        "all_passed": all(c["passed"] for c in checks),
    }
    (REPORTS / "design-data-profile.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {k: v for k, v in report.items() if k not in ("profiles", "checks")}, ensure_ascii=False, indent=2
        )
    )
    print(f"Checks: {sum(c['passed'] for c in checks)}/{len(checks)}")
    if not report["all_passed"]:
        raise SystemExit("Source findings require review; see reports/design-data-profile.json")


if __name__ == "__main__":
    main()
