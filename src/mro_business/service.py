"""Three read-only business tools over the backend's Flyway V1/V2 tables."""

import base64
import hashlib
import json
import logging
import time
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

from jsonschema import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from .contracts import INPUTS, OUTPUTS

LOG = logging.getLogger(__name__)

# repair_requests has no search-scope version yet (team v0.3 section 7); every request is version 1.
CONTEXT_VERSION = 1
# Team v0.3 status -> spec v1.0 status. CLOSED is added to the B01 contract (contracts.py).
STATUS_MAP = {
    "QUEUED": "RECEIVED",
    "CLASSIFYING": "TRIAGING",
    "INVESTIGATING": "INVESTIGATING",
    "REVIEWING": "REVIEWING",
    "ASSIGNED": "ASSIGNED",
    "ACCEPTED": "ACCEPTED",
    "HUMAN_REVIEW": "HUMAN_REVIEW",
    "CLOSED": "CLOSED",
}
NO_LOCKED_FILTERS = {"jasc_codes": [], "difficulty_from": None, "difficulty_to": None}
INSUFFICIENT_PRIVILEGE = "42501"


class ToolError(Exception):
    def __init__(self, code, message, *, status="ERROR", missing=None, retryable=False):
        self.code, self.message = code, message
        self.status, self.missing, self.retryable = status, missing or [], retryable


def iso(value):
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z") if value else None


def empty_result(name, args):
    return {
        "status": "OK",
        "request_id": args.get("request_id") if isinstance(args.get("request_id"), str) else None,
        "context_version": None,
        "trace_id": str(uuid4()),
        "applied_scope": None,
        "snapshot_ids": [],
        "counts": {"scope_count": None, "eligible_count": None, "returned_count": 0},
        "next_cursor": None,
        "warnings": [],
        "missing_fields": [],
        "error": None,
        **({"result": None} if name == "get_request_context" else {"items": []}),
    }


def encode_cursor(payload):
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(value):
    try:
        return json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ToolError("INVALID_ARGUMENT", "cursor is not a server-issued cursor") from exc


def history_filter_key(request_id, args):
    keys = ("occurred_from", "occurred_to", "query")
    raw = json.dumps([request_id, *(args.get(k) for k in keys)], ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class BusinessService:
    def __init__(self, engine, source_version="backend-seed"):
        self.engine, self.source_version = engine, source_version

    async def invoke(self, name, args):
        if name not in INPUTS:
            raise ToolError("UNKNOWN_TOOL", "Unknown tool")
        out = empty_result(name, args)
        started = time.monotonic()
        try:
            INPUTS[name].validate(args)
            if "query" in args and not args["query"].strip():
                raise ToolError("INVALID_ARGUMENT", "query must not be blank")
            try:
                request_id = str(UUID(args["request_id"]))
            except ValueError as exc:
                raise ToolError("INVALID_ARGUMENT", "request_id must be a repair_requests UUID") from exc
            if name != "get_request_context" and args["context_version"] != CONTEXT_VERSION:
                raise ToolError(
                    "CONTEXT_VERSION_CONFLICT",
                    f"context_version is {CONTEXT_VERSION}; call get_request_context again",
                )
            async with self.engine.connect() as conn, conn.begin():
                await conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
                await conn.execute(text("SET LOCAL statement_timeout = '8s'"))
                request = await self.load_request(conn, request_id)
                out["context_version"] = CONTEXT_VERSION
                if name == "get_request_context":
                    await self.request_context(conn, request, out)
                elif name == "get_aircraft_history":
                    await self.aircraft_history(conn, request, args, out)
                else:
                    await self.departments(conn, args, out)
        except ValidationError:
            self.error(out, ToolError("INVALID_ARGUMENT", "Input does not match the tool contract"))
        except ToolError as exc:
            self.error(out, exc)
        except Exception:
            LOG.exception("Business tool failure trace=%s", out["trace_id"])
            self.error(
                out,
                ToolError("INTERNAL_ERROR", "Lookup failed; use trace_id for investigation", retryable=True),
            )
        # A projection bug must fail closed without exposing DB/stack details.
        try:
            OUTPUTS[name].validate(out)
        except ValidationError:
            LOG.exception("Output contract violation trace=%s", out["trace_id"])
            out = empty_result(name, {})
            self.error(out, ToolError("OUTPUT_CONTRACT_ERROR", "Response contract validation failed"))
        LOG.info(
            "tool=%s trace=%s status=%s returned=%s error=%s ms=%d",
            name,
            out["trace_id"],
            out["status"],
            out["counts"]["returned_count"],
            (out["error"] or {}).get("code"),
            (time.monotonic() - started) * 1000,
        )
        return out

    @staticmethod
    def error(out, exc):
        out.update(
            status=exc.status,
            missing_fields=exc.missing,
            next_cursor=None,
            error={
                "code": exc.code,
                "message": exc.message,
                "retryable": exc.retryable,
                "retry_after_ms": None,
            },
        )
        if "items" in out:
            out["items"] = []
        else:
            out["result"] = None
        out["counts"]["returned_count"] = 0

    @staticmethod
    async def load_request(conn, request_id):
        try:
            row = (
                await conn.execute(
                    text(
                        "SELECT id, tail_number, aircraft_model, airline_code, symptom_text, status, "
                        "created_at, version FROM repair_requests WHERE id = CAST(:id AS uuid)"
                    ),
                    {"id": request_id},
                )
            ).one_or_none()
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) == INSUFFICIENT_PRIVILEGE:
                raise ToolError(
                    "UPSTREAM_UNAVAILABLE",
                    "mro_business_reader has no SELECT grant on repair_requests (team v0.3 section 7)",
                ) from exc
            raise
        if row is None:
            raise ToolError("NOT_FOUND", "No repair request with this request_id")
        return row

    async def request_context(self, conn, request, out):
        aircraft = (
            await conn.execute(
                text(
                    "SELECT tail_number, aircraft_model, airline_code, active FROM aircraft "
                    "WHERE tail_number = :tail"
                ),
                {"tail": request.tail_number},
            )
        ).one_or_none()
        missing = []
        if aircraft is None:
            missing.append("tail_number")
        else:
            if aircraft.airline_code != request.airline_code:
                missing.append("airline_code")
            if aircraft.aircraft_model != request.aircraft_model:
                missing.append("aircraft_model")
            if not aircraft.active:
                missing.append("aircraft_active")
        model_key = None if missing else request.aircraft_model.replace("-", "")
        out["result"] = {
            "report": {
                "symptom": request.symptom_text,
                "reporter_role": "other",
                "reported_at": iso(request.created_at),
            },
            "aircraft": {
                "aircraft_id": request.tail_number,
                "registration": request.tail_number,
                "model_raw": request.aircraft_model,
            },
            "resolved_scope": {
                "operator_code": None,
                "model_key": model_key,
                "operator_filter_applied": False,
                "model_filter_applied": False,
                "locked_filters": NO_LOCKED_FILTERS,
            },
            "context_version": CONTEXT_VERSION,
            # JPA @Version starts at 0; the contract requires >= 1.
            "request_version": request.version + 1,
            "status": STATUS_MAP[request.status],
            "missing_fields": missing,
            "locked_filters": NO_LOCKED_FILTERS,
        }
        out["missing_fields"] = missing
        out["counts"].update(scope_count=1, eligible_count=1, returned_count=1)
        out["warnings"] += [
            "operator_code is null: SDR operator mapping for KAL/AAR/JNA is undecided (team v0.3 section 7)",
            "reporter_role is not recorded by the backend; reported as other",
        ]
        if missing:
            out["status"] = "NEEDS_INPUT"
            out["warnings"].append("Request fields conflict with the aircraft master; model_key withheld")

    async def aircraft_history(self, conn, request, args, out):
        filter_key = history_filter_key(str(request.id), args)
        clauses = ["tail_number = :tail", "reported_at < :before"]
        params = {"tail": request.tail_number, "before": request.created_at}
        scope_count = await conn.scalar(
            text(f"SELECT count(*) FROM maintenance_history WHERE {' AND '.join(clauses)}"), params
        )
        if args.get("occurred_from"):
            clauses.append("reported_at >= :from_day")
            params["from_day"] = datetime.combine(
                date.fromisoformat(args["occurred_from"]), datetime.min.time(), UTC
            )
        if args.get("occurred_to"):
            clauses.append("reported_at < :to_day")
            params["to_day"] = datetime.combine(
                date.fromisoformat(args["occurred_to"]) + timedelta(days=1), datetime.min.time(), UTC
            )
        eligible_count = await conn.scalar(
            text(f"SELECT count(*) FROM maintenance_history WHERE {' AND '.join(clauses)}"), params
        )
        if args.get("cursor"):
            cursor = decode_cursor(args["cursor"])
            if cursor.get("k") != filter_key:
                raise ToolError("INVALID_ARGUMENT", "cursor belongs to a different request or filter")
            clauses.append("(reported_at, id) < (:c_at, :c_id)")
            params.update(c_at=datetime.fromisoformat(cursor["t"]), c_id=cursor["id"])
        limit = args.get("limit", 20)
        rows = (
            await conn.execute(
                text(
                    "SELECT id, tail_number, reported_at, symptom_text, ata_chapter, jasc_code, action_taken, "
                    f"department_code, closed_at FROM maintenance_history WHERE {' AND '.join(clauses)} "
                    "ORDER BY reported_at DESC, id DESC LIMIT :lim"
                ),
                {**params, "lim": limit + 1},
            )
        ).all()
        page, more = rows[:limit], len(rows) > limit
        out["items"] = [self.history_item(row) for row in page]
        out["counts"].update(scope_count=scope_count, eligible_count=eligible_count, returned_count=len(page))
        out["snapshot_ids"] = [self.source_version]
        if more:
            last = page[-1]
            out["next_cursor"] = encode_cursor(
                {"k": filter_key, "t": last.reported_at.isoformat(), "id": last.id}
            )
        out["status"] = "OK" if page else "NO_DATA" if scope_count == 0 else "NO_MATCH"
        out["warnings"].append(
            f"Only history reported before this request ({iso(request.created_at)}) is included"
        )
        if args.get("query"):
            out["warnings"].append("query does not filter or rank history; compare symptoms yourself")

    def history_item(self, row):
        observations = [f"ata_chapter={row.ata_chapter}"]
        if row.jasc_code:
            observations.append(f"jasc_code={row.jasc_code}")
        return {
            "evidence_id": row.id,
            "evidence_group_id": row.id,
            "source_type": "mock_aircraft_history",
            "source_id": row.id,
            "source_version": self.source_version,
            "locator": {
                "source_url": None,
                "file_id": "maintenance_history",
                "record_key": row.id,
                "section": "symptom_text",
                "start_offset": 0,
                "end_offset": len(row.symptom_text),
            },
            "quote": row.symptom_text,
            "history_id": row.id,
            "aircraft_id": row.tail_number,
            "symptom": row.symptom_text,
            "observations": observations,
            "actions": [row.action_taken] if row.action_taken else [],
            "outcome": f"closed_at={iso(row.closed_at)}" if row.closed_at else "open",
            "department_id": row.department_code,
            "occurred_at": iso(row.reported_at),
            "mock": True,
            "source_sdr_ids": [],
        }

    @staticmethod
    async def departments(conn, args, out):
        rows = (
            await conn.execute(text("SELECT code, name, ata_chapters, active FROM departments ORDER BY code"))
        ).all()
        active = [row for row in rows if row.active]
        hint_atas = {code[:2] for code in args.get("candidate_jasc", [])}
        query = args.get("query", "")

        def chapters(row):
            return [c.strip() for c in row.ata_chapters.split(",") if c.strip()]

        def score(row):
            return 2 * len(hint_atas & set(chapters(row))) + (1 if row.name in query else 0)

        ranked = sorted(active, key=lambda row: (-score(row), row.code))
        limit = args.get("limit", 5)
        out["items"] = [
            {
                "department_id": row.code,
                "name": row.name,
                "responsibilities": [f"ATA {c}" for c in chapters(row)],
                "supported_models": [],
                "scope_constraints": [],
                "availability_status": "available",
                "mock": True,
            }
            for row in ranked[:limit]
        ]
        out["counts"].update(
            scope_count=len(rows), eligible_count=len(active), returned_count=len(out["items"])
        )
        out["status"] = "OK" if active else "NO_DATA"
        out["warnings"].append("supported_models is empty: every department handles every aircraft model")
        if hint_atas:
            out["warnings"].append(
                "candidate_jasc ranks departments by the code's first two digits (ATA); it is a hint, not an answer"
            )
        if len(active) > limit:
            out["warnings"].append(
                f"{len(active) - limit} active departments omitted; raise limit to see all"
            )
