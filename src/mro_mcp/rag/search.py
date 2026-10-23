"""Search logic for R01/R02/R03/R04/R05. Every query is authorized and pinned to an immutable run.

Moved from mro_rag/service.py: input/output validation and error envelopes now live in common.McpServer;
queries, scoping, cursors and ranking are unchanged.
"""

import base64
import hashlib
import hmac
import json
import re
import time
from datetime import UTC, date, datetime

from sqlalchemy import and_, case, false, func, select, text

from mro_rag import db
from mro_rag.ingest import json_hash

from ..common.db import ReadOnlyDatabase
from ..common.errors import ToolFailure
from . import sdr_vectors, tech
from .sdr_vectors import MODEL_VERSION

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


def any_word_tsquery(text):
    """Explore mode: rank rows matching ANY query word (more words matched rank higher).

    websearch_to_tsquery alone requires every word, so a planner's natural 4-word query
    ("exit sign light chime") matched nothing. Only ASCII words are kept (SDR/JASC text is English),
    which also drops quotes and search operators from the input.
    """
    words = re.findall(r"[A-Za-z0-9]+", text)
    if not words:
        return func.websearch_to_tsquery("english", text)
    return func.websearch_to_tsquery("english", " or ".join(words))


class RagSearch:
    def __init__(self, database: ReadOnlyDatabase, cursor_secret, query_encoder=None):
        if len(cursor_secret) < 32:
            raise ValueError("Cursor secret must be at least 32 characters")
        self.database, self.secret = database, cursor_secret.encode()
        # One OpenAI query encoder for every semantic search (R02 SDR, R04 articles); None means lexical only.
        self.query_encoder = query_encoder

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
                elif name == "search_technical_docs":
                    await self.tech_search(conn, ctx, args, out)
                elif name == "get_sdr_amm_references":
                    await self.sdr_amm(conn, ctx, args, out)
                elif args["evidence_id"].startswith("EV-DOC-"):
                    await self.tech_evidence(conn, ctx, args, out)
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
            ts = any_word_tsquery(args["query"])
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

    RRF_K = 60  # reciprocal rank fusion constant (Cormack et al. 2009)
    FUSION_POOL = 50  # candidates taken from each half before fusing

    async def sdr_vector_ready(self, conn, ctx, out):
        """Semantic half only when documents were embedded with the encoder this server queries with."""
        if self.query_encoder is None:
            out["warnings"].append("VECTOR_UNAVAILABLE: no query encoder configured; lexical ranking only")
            return False
        if await conn.scalar(text("SELECT to_regclass('rag.sdr_embeddings')")) is None:
            out["warnings"].append("VECTOR_UNAVAILABLE: SDR embeddings are not restored; lexical ranking only")
            return False
        e = sdr_vectors.embeddings
        ready = await conn.scalar(
            select(e.c.sdr_case_uuid)
            .where(e.c.release_id == ctx["release_id"], e.c.embedding_model_version == self.query_encoder.version)
            .limit(1)
        )
        if ready is None:
            out["warnings"].append("VECTOR_UNAVAILABLE: no embeddings for this release and model; lexical ranking only")
        return ready is not None

    async def sdr_search(self, conn, ctx, args, out):
        await self.ready_index(conn, ctx, "sdr", "LEXICAL")
        for key, locked in [("scope_count", False), ("eligible_count", True)]:
            out["counts"][key] = await conn.scalar(
                select(func.count()).select_from(db.sdr).where(*self.scope(ctx, locked))
            )
        if args.get("search_mode") == "exact":
            ts = func.phraseto_tsquery("english", args["query"])
        else:
            ts = any_word_tsquery(args["query"])
        boost = case((db.sdr.c.reported_jasc.in_(args.get("candidate_jasc", [])), 0.03), else_=0.0)
        score = (func.ts_rank_cd(db.sdr.c.search_document, ts) + boost).label("rank")
        offset = self.page_offset("search_sdr_cases", ctx, args)
        vector = None
        if args.get("search_mode") != "exact" and await self.sdr_vector_ready(conn, ctx, out):
            try:
                vector = await self.query_encoder.encode(args["query"])
            except Exception as exc:  # noqa: BLE001 - the lexical half still answers
                out["warnings"].append(f"VECTOR_UNAVAILABLE: query embedding failed ({type(exc).__name__})")
        if vector is None:
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
            kind = "fusion" if args.get("candidate_jasc") else "lexical"
        else:
            rows = await self.sdr_fused(conn, ctx, args, ts, score, boost, vector, offset)
            kind = "fusion"
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
                score_kind=kind,
                difficulty_date=row["difficulty_date"].isoformat(),
                submission_date=row["submission_date"].isoformat(),
                narrative_excerpt=row["narrative_raw"],
                component="; ".join(f"{k}: {v}" for k, v in row["component"].items() if v) or None,
                reported_actions=actions[:30],
                cited_references=list(refs[:30]),
            )
            out["items"].append(item)


    async def sdr_fused(self, conn, ctx, args, ts, lexical_score, boost, vector, offset):
        """Reciprocal rank fusion of the lexical and semantic rankings, both inside the pinned scope."""
        e = sdr_vectors.embeddings
        pool = max(self.FUSION_POOL, offset + args["limit"] + 1)
        lexical = (
            await conn.execute(
                select(db.sdr.c.id, db.sdr.c.case_id)
                .where(*self.scope(ctx), db.sdr.c.search_document.op("@@")(ts))
                .order_by(lexical_score.desc(), db.sdr.c.case_id)
                .limit(pool)
            )
        ).all()
        similarity = (1 - e.c.embedding.cosine_distance(vector)) + boost
        semantic = (
            await conn.execute(
                select(db.sdr.c.id, db.sdr.c.case_id)
                .select_from(
                    db.sdr.join(e, and_(e.c.sdr_case_uuid == db.sdr.c.id, e.c.release_id == db.sdr.c.release_id))
                )
                .where(*self.scope(ctx), e.c.embedding_model_version == self.query_encoder.version)
                .order_by(similarity.desc(), db.sdr.c.case_id)
                .limit(pool)
            )
        ).all()
        fused, case_ids = {}, {}
        for ranking in (lexical, semantic):
            for position, (sdr_id, case_id) in enumerate(ranking, start=1):
                fused[sdr_id] = fused.get(sdr_id, 0.0) + 1.0 / (self.RRF_K + position)
                case_ids[sdr_id] = case_id
        ordered = sorted(fused, key=lambda i: (-fused[i], case_ids[i]))[offset : offset + args["limit"] + 1]
        if not ordered:
            return []
        found = {
            row["id"]: row
            for row in (await conn.execute(select(db.sdr).where(db.sdr.c.id.in_(ordered)))).mappings().all()
        }
        return [{**found[i], "rank": fused[i]} for i in ordered]

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
        # Number lookup only: AMM is a handoff reference, so the semantic search over citation chunks is gone (V8).
        await self.ready_index(conn, ctx, "derived_amm", "EXACT")
        query = select(db.amm).select_from(source).where(*filters).order_by(db.amm.c.distilled_record_id)
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
                score=None,
                score_kind="exact",
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

    async def tech_scope(self, conn, ctx, out, include_unknown):
        """Pins the article snapshot and returns {document_id: applies} for articles in the model's family.

        Public articles have no operator, so the operator filter is never applied (the AI rejects it).
        """
        if await conn.scalar(text("SELECT to_regclass('public.tech_documents')")) is None:
            raise ToolFailure("INDEX_NOT_READY", "Technical articles are not restored")
        models = set((await conn.execute(select(tech.embeddings.c.embedding_model_version).distinct())).scalars())
        if models - {MODEL_VERSION}:
            raise ToolFailure(
                "INDEX_VERSION_UNAVAILABLE", "Server encoder does not match the article embeddings"
            )
        d = tech.documents
        versions = (await conn.execute(select(d.c.document_id, d.c.source_version).order_by(d.c.document_id))).all()
        out["snapshot_ids"] = [tech.snapshot_id([f"{doc}:{version}" for doc, version in versions], MODEL_VERSION)]
        out["applied_scope"]["operator_filter_applied"] = False
        rows = (
            (
                await conn.execute(
                    select(d.c.document_id, d.c.title, d.c.applicability_basis, d.c.configuration_conditions).where(
                        d.c.category == tech.SEARCH_CATEGORY
                    )
                )
            )
            .mappings()
            .all()
        )
        scope = {}
        for row in rows:
            conditions = " ".join(str(x) for x in row["configuration_conditions"] or [])
            verdict = tech.applies(ctx["model_key"], row["title"], row["applicability_basis"], conditions)
            if verdict is True or (verdict is None and include_unknown):
                scope[row["document_id"]] = verdict
        return scope

    @staticmethod
    def tech_locator(row):
        return {
            "source_url": row["url"],
            "file_id": row["document_id"],
            "record_key": row["chunk_id"],
            "section": row["section"],
            "start_offset": row["start_offset"],
            "end_offset": row["end_offset"],
        }

    async def tech_search(self, conn, ctx, args, out):
        include_unknown = args.get("include_unknown_applicability", False)
        scope = await self.tech_scope(conn, ctx, out, include_unknown)
        c, d, e = tech.chunks, tech.documents, tech.embeddings
        in_scope = c.c.document_id.in_(list(scope)) if scope else false()
        eligible = [in_scope]
        if not include_unknown:
            # Contract: an unknown paragraph is never promoted to general applicability evidence.
            eligible.append(c.c.applicability_status.in_(("supported", "conditional")))
        out["counts"]["scope_count"] = await conn.scalar(select(func.count()).select_from(c).where(in_scope))
        out["counts"]["eligible_count"] = await conn.scalar(select(func.count()).select_from(c).where(*eligible))
        keywords = args.get("system_keywords", [])
        bonus = (
            case((c.c.search_document.op("@@")(any_word_tsquery(" ".join(keywords))), 0.03), else_=0.0)
            if keywords
            else 0.0
        )
        vector = None
        # Stored article vectors for the eligible chunks: without them the vector JOIN would silently
        # return nothing (NO_MATCH) even though the articles exist.
        embedded = await conn.scalar(
            select(func.count())
            .select_from(c.join(e, and_(e.c.chunk_id == c.c.chunk_id, e.c.embedding_model_version == MODEL_VERSION)))
            .where(*eligible)
        )
        if self.query_encoder is None:
            out["warnings"].append("VECTOR_UNAVAILABLE: no query encoder configured; lexical ranking only")
        elif not embedded and out["counts"]["eligible_count"]:
            out["warnings"].append("VECTOR_UNAVAILABLE: article embeddings are not restored; lexical ranking only")
        else:
            if embedded < out["counts"]["eligible_count"]:
                out["warnings"].append(
                    f"VECTOR_PARTIAL: {embedded} of {out['counts']['eligible_count']} eligible chunks have embeddings"
                )
            try:
                vector = await self.query_encoder.encode(args["query"])
            except Exception as exc:  # noqa: BLE001 - the lexical ranking still answers
                out["warnings"].append(f"VECTOR_UNAVAILABLE: query embedding failed ({type(exc).__name__})")
        source = c.join(d, d.c.document_id == c.c.document_id)
        if vector is not None:
            score = (1 - e.c.embedding.cosine_distance(vector) + bonus).label("rank")
            source = source.join(e, and_(e.c.chunk_id == c.c.chunk_id, e.c.embedding_model_version == MODEL_VERSION))
            kind = "fusion" if keywords else "vector"
        else:
            ts = any_word_tsquery(" ".join([args["query"], *keywords]))
            score = (func.ts_rank_cd(c.c.search_document, ts) + bonus).label("rank")
            eligible.append(c.c.search_document.op("@@")(ts))
            kind = "lexical"
        offset = self.page_offset("search_technical_docs", ctx, args)
        rows = (
            (
                await conn.execute(
                    select(c, d.c.title, d.c.url, d.c.source_version, score)
                    .select_from(source)
                    .where(*eligible)
                    .order_by(score.desc(), c.c.chunk_id)
                    .offset(offset)
                    .limit(args["limit"] + 1)
                )
            )
            .mappings()
            .all()
        )
        selected = self.finish_page("search_technical_docs", ctx, args, out, rows, offset)
        for row in selected:
            out["items"].append(
                {
                    "evidence_id": row["evidence_id"],
                    "evidence_group_id": row["evidence_group_id"],
                    "source_type": "airbus_technical_article",
                    "source_id": row["document_id"],
                    "source_version": row["source_version"],
                    "locator": self.tech_locator(row),
                    "quote": row["text"],
                    "score": float(row["rank"]),
                    "score_kind": kind,
                    "document_id": row["document_id"],
                    "chunk_id": row["chunk_id"],
                    "title": row["title"],
                    "url": row["url"],
                    "section": row["section"],
                    "excerpt": row["text"],
                    # An article that names no aircraft type is unknown, whatever its paragraph says.
                    "applicability_status": row["applicability_status"] if scope[row["document_id"]] else "unknown",
                    "applicability_basis": row["applicability_basis"],
                    "configuration_conditions": [
                        str(x)[:2000] for x in row["configuration_conditions"] or []
                    ][:30],
                }
            )
        if selected:
            out["warnings"].append(
                "TECHNICAL_REFERENCE_ONLY: public Airbus article; applicability to this aircraft is not verified"
            )
        if any(item["applicability_status"] == "unknown" for item in out["items"]):
            out["warnings"].append("APPLICABILITY_UNKNOWN: unknown items are reference only")

    async def tech_evidence(self, conn, ctx, args, out):
        # R05 re-checks any article the search could return, unknown applicability included.
        scope = await self.tech_scope(conn, ctx, out, include_unknown=True)
        c, d = tech.chunks, tech.documents
        row = (
            (
                await conn.execute(
                    select(c, d.c.url, d.c.source_version, d.c.body_text)
                    .select_from(c.join(d, d.c.document_id == c.c.document_id))
                    .where(c.c.evidence_id == args["evidence_id"])
                )
            )
            .mappings()
            .first()
        )
        if not row or row["document_id"] not in scope:
            out["status"] = "NO_MATCH"
            return
        start, end = row["start_offset"], row["end_offset"]
        body = row["body_text"][start:end]
        out["result"] = {
            "evidence_id": row["evidence_id"],
            "evidence_group_id": row["evidence_group_id"],
            "source_type": "airbus_technical_article",
            "source_id": row["document_id"],
            "source_version": row["source_version"],
            "quote": row["text"],
            "locator": self.tech_locator(row),
            "text": body,
            "parent_source_ids": [],
            "quote_spans": [{"start": start, "end": end, "text": body}],
        }
        out["counts"]["returned_count"] = 1

    MAX_SDR_AMM = 100

    async def sdr_amm(self, conn, ctx, args, out):
        """AMM numbers cited in SDR cases already used as evidence: a lookup for the handoff, not a search.

        Only SDR cases inside the pinned run scope are read, the same rule R05 applies.
        """
        ev = db.evidence
        cited = (
            select(ev.c.evidence_id.label("sdr_evidence_id"), ev.c.sdr_case_uuid)
            .where(
                ev.c.release_id == ctx["release_id"],
                ev.c.source_type == "sdr",
                ev.c.evidence_id.in_(args["evidence_ids"]),
            )
            .subquery()
        )
        found = (
            await conn.execute(
                select(cited.c.sdr_evidence_id)
                .select_from(
                    cited.join(
                        db.sdr,
                        and_(db.sdr.c.id == cited.c.sdr_case_uuid, db.sdr.c.release_id == ctx["release_id"]),
                    )
                )
                .where(*self.scope(ctx))
            )
        ).scalars().all()
        out["counts"].update(scope_count=len(args["evidence_ids"]), eligible_count=len(found))
        missing = sorted(set(args["evidence_ids"]) - set(found))
        if missing:
            out["warnings"].append("SDR_NOT_IN_SCOPE: " + ", ".join(missing)[:1900])
        source = db.amm.join(cited, cited.c.sdr_case_uuid == db.amm.c.sdr_case_uuid).join(
            db.sdr, and_(db.sdr.c.id == db.amm.c.sdr_case_uuid, db.sdr.c.release_id == db.amm.c.release_id)
        )
        rows = (
            (
                await conn.execute(
                    select(db.amm, cited.c.sdr_evidence_id)
                    .select_from(source)
                    .where(db.amm.c.release_id == ctx["release_id"], *self.scope(ctx))
                    .order_by(cited.c.sdr_evidence_id, db.amm.c.distilled_record_id)
                    .limit(self.MAX_SDR_AMM + 1)
                )
            )
            .mappings()
            .all()
        )
        if len(rows) > self.MAX_SDR_AMM:
            out["warnings"].append(f"SUMMARY_LIST_TRUNCATED: first {self.MAX_SDR_AMM} references only")
        for row in rows[: self.MAX_SDR_AMM]:
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
                sdr_evidence_id=row["sdr_evidence_id"],
                locator=locator(p["locator"]),
                score=None,
                score_kind="exact",
            )
            out["items"].append(item)
        out["counts"]["returned_count"] = len(out["items"])
        out["status"] = "OK" if out["items"] else "NO_MATCH"
        if out["items"]:
            out["warnings"].append(
                "AMM_REFERENCE_ONLY: reference numbers cited in the SDR text; procedure and applicability are not verified"
            )
