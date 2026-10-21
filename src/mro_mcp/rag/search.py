"""Search logic for R01/R02/R03/R05. Every query is authorized and pinned to an immutable run.

Moved from mro_rag/service.py: input/output validation and error envelopes now live in common.McpServer;
queries, scoping, cursors and ranking are unchanged.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import re
import time
from datetime import UTC, date, datetime

from sqlalchemy import and_, case, func, select

from mro_rag import db
from mro_rag.embedding import CONFIG, LocalEncoder
from mro_rag.ingest import json_hash

from ..common.db import ReadOnlyDatabase
from ..common.errors import ToolFailure

CARRIED = ("applied_scope", "snapshot_ids", "counts", "warnings")


def locator(value):
    return {
        k: value.get(k)
        for k in ("source_url", "file_id", "record_key", "section", "start_offset", "end_offset")
    }


def new_result(name, args):
    return {
        "status": "OK",
        "request_id": args["request_id"],
        "context_version": args["context_version"],
        "applied_scope": None,
        "snapshot_ids": [],
        "counts": {"scope_count": None, "eligible_count": None, "returned_count": 0},
        "next_cursor": None,
        "warnings": [],
        **({"result": None} if name == "get_evidence" else {"items": []}),
    }


class RagSearch:
    def __init__(self, database: ReadOnlyDatabase, cursor_secret, encoder=None):
        if len(cursor_secret) < 32:
            raise ValueError("Cursor secret must be at least 32 characters")
        self.database, self.secret, self.encoder = database, cursor_secret.encode(), encoder
        self.encode_lock = asyncio.Lock()

    async def preload_encoder(self):
        """Load the pinned model before accepting calls with a ten-second client timeout."""
        async with self.encode_lock:
            if self.encoder is None:
                self.encoder = await asyncio.to_thread(LocalEncoder)
            await asyncio.to_thread(self.encoder.encode, ["maintenance"], query=True)

    async def run(self, name, args, claims):
        """Returns the response dict for one tool call; raises ToolFailure for caller-actionable outcomes."""
        out = new_result(name, args)
        async with self.database.read() as conn:
            ctx = await self.resolve(conn, args, claims)
            try:
                out["snapshot_ids"] = [ctx["release_key"], *ctx["index_ids"]]
                scoped = name != "search_jasc_codes"
                out["applied_scope"] = {
                    "operator_code": ctx["operator_code"],
                    "model_key": ctx["model_key"],
                    # Demo policy (team C02): a context without operator pins the model only.
                    "operator_filter_applied": scoped and ctx["operator_code"] is not None,
                    "model_filter_applied": scoped,
                    "locked_filters": ctx["locked_filters"],
                }
                if scoped and not ctx["model_key"]:
                    raise ToolFailure(
                        "MISSING_SCOPE",
                        "Verified model mapping is required",
                        status="NEEDS_INPUT",
                        missing=["model_key"],
                    )
                await self.validate_codes(conn, ctx, args)
                if name == "search_jasc_codes":
                    await self.jasc_search(conn, ctx, args, out)
                elif name == "search_sdr_cases":
                    await self.sdr_search(conn, ctx, args, out)
                elif name == "search_amm_references":
                    await self.amm_search(conn, ctx, args, out)
                else:
                    await self.get_evidence(conn, ctx, args, out)
            except ToolFailure as failure:
                # Keep the scope that was already resolved so the caller sees what was applied.
                if not failure.details:
                    failure.details = {k: out[k] for k in CARRIED}
                raise
        return out

    async def resolve(self, conn, args, claims):
        if not claims or args["request_id"] != claims["request_id"]:
            raise ToolFailure("FORBIDDEN", "Request is not authorized")
        if args["context_version"] != claims["context_version"]:
            raise ToolFailure("CONTEXT_VERSION_CONFLICT", "Request context version changed")
        row = (
            (
                await conn.execute(
                    select(db.contexts).where(
                        db.contexts.c.run_id == claims["run_id"],
                        db.contexts.c.request_id == claims["request_id"],
                        db.contexts.c.subject_id == claims["sub"],
                        db.contexts.c.tenant_id == claims["tenant_id"],
                    )
                )
            )
            .mappings()
            .first()
        )
        if not row or not row["active"] or row["expires_at"] <= datetime.now(UTC):
            raise ToolFailure("FORBIDDEN", "Run is unavailable or revoked")
        if row["context_version"] != args["context_version"]:
            raise ToolFailure("CONTEXT_VERSION_CONFLICT", "Request context version changed")
        release = (
            (await conn.execute(select(db.releases).where(db.releases.c.id == row["release_id"])))
            .mappings()
            .first()
        )
        if not release or release["state"] != "READY":
            raise ToolFailure("INDEX_VERSION_UNAVAILABLE", "Pinned release is unavailable")
        return dict(row, release_key=release["release_key"])

    async def ready_index(self, conn, ctx, dataset, method):
        rows = (
            (
                await conn.execute(
                    select(db.indexes).where(
                        db.indexes.c.release_id == ctx["release_id"],
                        db.indexes.c.index_key.in_(ctx["index_ids"]),
                        db.indexes.c.dataset == dataset,
                        db.indexes.c.method == method,
                    )
                )
            )
            .mappings()
            .all()
        )
        if not rows:
            raise ToolFailure("INDEX_NOT_READY", "No pinned index for this search method")
        if len(rows) != 1 or rows[0]["state"] != "READY":
            raise ToolFailure("INDEX_VERSION_UNAVAILABLE", "Pinned index is unavailable or ambiguous")
        return rows[0]

    async def validate_codes(self, conn, ctx, args):
        codes = set(
            args.get("codes", [])
            + args.get("candidate_jasc", [])
            + ctx["locked_filters"].get("jasc_codes", [])
        )
        if codes:
            existing = set(
                (
                    await conn.execute(
                        select(db.jasc.c.code).where(
                            db.jasc.c.release_id == ctx["release_id"], db.jasc.c.code.in_(codes)
                        )
                    )
                ).scalars()
            )
            if existing != codes:
                raise ToolFailure(
                    "INVALID_JASC", "One or more JASC codes do not exist in the pinned codebook"
                )

    def scope(self, ctx, locked=True):
        conditions = [db.sdr.c.release_id == ctx["release_id"], db.sdr.c.model_key == ctx["model_key"]]
        if ctx["operator_code"] is not None:
            conditions.append(db.sdr.c.operator_code == ctx["operator_code"])
        if locked:
            f = ctx["locked_filters"]
            if f.get("jasc_codes"):
                conditions.append(db.sdr.c.reported_jasc.in_(f["jasc_codes"]))
            for key, operator in [
                ("difficulty_from", db.sdr.c.difficulty_date.__ge__),
                ("difficulty_to", db.sdr.c.difficulty_date.__le__),
            ]:
                if f.get(key):
                    conditions.append(operator(date.fromisoformat(f[key])))
        return conditions

    def cursor_binding(self, name, ctx, args):
        return json_hash(
            {
                "tool": name,
                "run": ctx["run_id"],
                "subject": ctx["subject_id"],
                "tenant": ctx["tenant_id"],
                "context_version": ctx["context_version"],
                "release": ctx["release_key"],
                "indexes": ctx["index_ids"],
                "operator": ctx["operator_code"],
                "model": ctx["model_key"],
                "filters": ctx["locked_filters"],
                "mapping": ctx["mapping_revision"],
                "args": {k: v for k, v in args.items() if k != "cursor"},
            }
        )

    def page_offset(self, name, ctx, args):
        if "cursor" not in args:
            return 0
        try:
            encoded, signature = args["cursor"].split(".")
            expected = hmac.new(self.secret, encoded.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, signature):
                raise ValueError()
            value = json.loads(base64.urlsafe_b64decode(encoded))
            if value["binding"] != self.cursor_binding(name, ctx, args) or value["expires"] < time.time():
                raise ValueError()
            if type(value["offset"]) is not int or not 0 <= value["offset"] <= 200000:
                raise ValueError()
            return value["offset"]
        except (ValueError, KeyError, TypeError):
            raise ToolFailure(
                "INVALID_CURSOR", "Cursor is invalid, expired, or belongs to another query"
            ) from None

    def finish_page(self, name, ctx, args, out, rows, offset):
        limit = args["limit"]
        if len(rows) > limit:
            value = {
                "binding": self.cursor_binding(name, ctx, args),
                "offset": offset + limit,
                "expires": min(time.time() + 1800, ctx["expires_at"].timestamp()),
            }
            encoded = base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).decode()
            out["next_cursor"] = (
                encoded + "." + hmac.new(self.secret, encoded.encode(), hashlib.sha256).hexdigest()
            )
        rows = rows[:limit]
        out["counts"]["returned_count"] = len(rows)
        out["status"] = "OK" if rows else "NO_DATA" if out["counts"]["scope_count"] == 0 else "NO_MATCH"
        return rows

    async def jasc_search(self, conn, ctx, args, out):
        await self.ready_index(conn, ctx, "jasc", "LEXICAL")
        base = db.jasc.c.release_id == ctx["release_id"]
        count = await conn.scalar(select(func.count()).select_from(db.jasc).where(base))
        out["counts"].update(scope_count=count, eligible_count=count)
        query = select(db.jasc).where(base)
        if "codes" in args:
            query = query.where(db.jasc.c.code.in_(args["codes"]))
        if "query" in args:
            ts = func.websearch_to_tsquery("english", args["query"])
            query = query.where(db.jasc.c.search_document.op("@@")(ts)).order_by(
                func.ts_rank_cd(db.jasc.c.search_document, ts).desc()
            )
        rows = (await conn.execute(query.order_by(db.jasc.c.code).limit(args["limit"]))).mappings().all()
        out["items"] = [
            {k: r[k] for k in ("code", "title", "definition", "source_url", "codebook_version")} for r in rows
        ]
        out["counts"]["returned_count"] = len(rows)
        out["status"] = "OK" if rows else "NO_DATA" if count == 0 else "NO_MATCH"
        if any(r["title_status"] != "official_pdf" for r in rows):
            out["warnings"].append(
                "JASC_TITLE_UNVERIFIED: source contains a code without a verified official title"
            )

    async def sdr_search(self, conn, ctx, args, out):
        await self.ready_index(conn, ctx, "sdr", "LEXICAL")
        for key, locked in [("scope_count", False), ("eligible_count", True)]:
            out["counts"][key] = await conn.scalar(
                select(func.count()).select_from(db.sdr).where(*self.scope(ctx, locked))
            )
        parser = func.phraseto_tsquery if args.get("search_mode") == "exact" else func.websearch_to_tsquery
        ts = parser("english", args["query"])
        boost = case((db.sdr.c.reported_jasc.in_(args.get("candidate_jasc", [])), 0.03), else_=0.0)
        score = (func.ts_rank_cd(db.sdr.c.search_document, ts) + boost).label("rank")
        offset = self.page_offset("search_sdr_cases", ctx, args)
        rows = (
            (
                await conn.execute(
                    select(db.sdr, score)
                    .where(*self.scope(ctx), db.sdr.c.search_document.op("@@")(ts))
                    .order_by(score.desc(), db.sdr.c.case_id)
                    .offset(offset)
                    .limit(args["limit"] + 1)
                )
            )
            .mappings()
            .all()
        )
        selected = self.finish_page("search_sdr_cases", ctx, args, out, rows, offset)
        for row in selected:
            p = row["source_payload"]
            refs = (
                (
                    await conn.execute(
                        select(db.amm.c.cited_amm_ref_normalized)
                        .where(db.amm.c.release_id == ctx["release_id"], db.amm.c.sdr_case_uuid == row["id"])
                        .distinct()
                        .order_by(db.amm.c.cited_amm_ref_normalized)
                    )
                )
                .scalars()
                .all()
            )
            actions = [x["text"] for x in row["reported_action_candidates"]]
            if len(refs) > 30 or len(actions) > 30:
                out["warnings"].append("SUMMARY_LIST_TRUNCATED: use get_evidence for the full source")
            item = {
                k: p[k]
                for k in (
                    "evidence_id",
                    "evidence_group_id",
                    "source_type",
                    "source_id",
                    "source_version",
                    "case_id",
                    "operator_code",
                    "model_key",
                    "reported_jasc",
                )
            }
            item.update(
                source_locator=locator(p["locator"]),
                quote=p["quote"],
                score=float(row["rank"]),
                score_kind="fusion" if args.get("candidate_jasc") else "lexical",
                difficulty_date=row["difficulty_date"].isoformat(),
                submission_date=row["submission_date"].isoformat(),
                narrative_excerpt=row["narrative_raw"],
                component="; ".join(f"{k}: {v}" for k, v in row["component"].items() if v) or None,
                reported_actions=actions[:30],
                cited_references=list(refs[:30]),
            )
            out["items"].append(item)

    async def query_vector(self, query):
        async with self.encode_lock:
            if self.encoder is None:
                self.encoder = await asyncio.to_thread(LocalEncoder)
            result = await asyncio.to_thread(self.encoder.encode, [query], query=True)
            return result[0]

    async def amm_search(self, conn, ctx, args, out):
        source = db.amm.join(
            db.sdr, and_(db.sdr.c.id == db.amm.c.sdr_case_uuid, db.sdr.c.release_id == db.amm.c.release_id)
        )
        for key, locked in [("scope_count", False), ("eligible_count", True)]:
            out["counts"][key] = await conn.scalar(
                select(func.count()).select_from(source).where(*self.scope(ctx, locked))
            )
        filters = self.scope(ctx)
        if args.get("reference_levels"):
            filters.append(db.amm.c.reference_level.in_(args["reference_levels"]))
        if "amm_reference_exact" in args:
            number = re.sub(r"\s*[-.–—]\s*", "-", args["amm_reference_exact"].strip()).upper()
            filters.append(db.amm.c.cited_amm_ref_normalized == number)
        offset = self.page_offset("search_amm_references", ctx, args)
        if "query" in args:
            index = await self.ready_index(conn, ctx, "derived_amm", "VECTOR")
            if index["config"] != CONFIG:
                raise ToolFailure(
                    "INDEX_VERSION_UNAVAILABLE",
                    "Server encoder does not match the pinned embedding configuration",
                )
            vector = await self.query_vector(args["query"])
            score = 1 - db.embeddings.c.embedding.cosine_distance(vector)
            source = source.join(
                db.links,
                and_(db.links.c.reference_uuid == db.amm.c.id, db.links.c.release_id == ctx["release_id"]),
            )
            source = source.join(
                db.embeddings,
                and_(
                    db.embeddings.c.chunk_uuid == db.links.c.chunk_uuid,
                    db.embeddings.c.index_uuid == index["id"],
                    db.embeddings.c.release_id == ctx["release_id"],
                ),
            )
            boost = case((db.sdr.c.reported_jasc.in_(args.get("candidate_jasc", [])), 0.03), else_=0.0)
            ranks = (
                select(db.amm.c.id.label("ref_id"), func.max(score + boost).label("rank"))
                .select_from(source)
                .where(*filters)
                .group_by(db.amm.c.id)
                .subquery()
            )
            query = (
                select(db.amm, ranks.c.rank)
                .join(ranks, ranks.c.ref_id == db.amm.c.id)
                .order_by(ranks.c.rank.desc(), db.amm.c.distilled_record_id)
            )
            kind = "fusion" if args.get("candidate_jasc") else "vector"
        else:
            await self.ready_index(conn, ctx, "derived_amm", "EXACT")
            query = select(db.amm).select_from(source).where(*filters).order_by(db.amm.c.distilled_record_id)
            kind = "exact"
        rows = (await conn.execute(query.offset(offset).limit(args["limit"] + 1))).mappings().all()
        selected = self.finish_page("search_amm_references", ctx, args, out, rows, offset)
        for row in selected:
            p = row["source_payload"]
            item = {
                k: p[k]
                for k in (
                    "evidence_id",
                    "evidence_group_id",
                    "source_type",
                    "source_id",
                    "source_version",
                    "distilled_record_id",
                    "source_case_id",
                    "cited_amm_ref_raw",
                    "cited_amm_ref_normalized",
                    "reference_level",
                    "symptom",
                    "reported_action",
                    "outcome",
                    "relationship_status",
                    "applicability_status",
                    "reported_jasc",
                    "quote",
                )
            }
            item.update(
                locator=locator(p["locator"]),
                score=float(row["rank"]) if "query" in args else None,
                score_kind=kind,
            )
            out["items"].append(item)
        if selected:
            out["warnings"].append(
                "AMM_REFERENCE_ONLY: applicability and semantic relationship are not verified"
            )

    async def get_evidence(self, conn, ctx, args, out):
        source = db.evidence.join(
            db.sdr,
            and_(db.sdr.c.id == db.evidence.c.sdr_case_uuid, db.sdr.c.release_id == db.evidence.c.release_id),
        )
        row = (
            (
                await conn.execute(
                    select(db.evidence, db.sdr.c.evidence_group_id, db.sdr.c.case_id)
                    .select_from(source)
                    .where(*self.scope(ctx), db.evidence.c.evidence_id == args["evidence_id"])
                )
            )
            .mappings()
            .first()
        )
        if not row:
            out["status"] = "NO_MATCH"
            return
        loc = locator(row["locator"])
        out["result"] = {
            k: row[k]
            for k in (
                "evidence_id",
                "evidence_group_id",
                "source_type",
                "source_id",
                "source_version",
                "quote",
            )
        }
        out["result"].update(
            locator=loc,
            text=row["quote"],
            parent_source_ids=[row["case_id"]] if row["source_type"] == "derived_amm_reference" else [],
            quote_spans=[{"start": loc["start_offset"], "end": loc["end_offset"], "text": row["quote"]}],
        )
        out["counts"]["returned_count"] = 1
