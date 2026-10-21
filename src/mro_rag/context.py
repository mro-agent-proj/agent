"""Backend-owned run projection and short-lived delegated authorization."""

from datetime import UTC, date, datetime, timedelta

import jwt
from sqlalchemy import insert, select, text, update

from . import db

AUDIENCE = "mro-rag"
ISSUER = "mro-backend"


def issue_token(secret, *, subject_id, tenant_id, run_id, request_id, context_version, seconds=600, audience=AUDIENCE):
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": subject_id,
            "tenant_id": tenant_id,
            "run_id": run_id,
            "request_id": request_id,
            "context_version": context_version,
            "iat": now,
            "exp": now + timedelta(seconds=seconds),
            "iss": ISSUER,
            "aud": audience,
        },
        secret,
        algorithm="HS256",
    )


def verify_token(secret, token, audience=AUDIENCE):
    claims = jwt.decode(
        token,
        secret,
        algorithms=["HS256"],
        audience=audience,
        issuer=ISSUER,
        options={"require": ["sub", "tenant_id", "run_id", "request_id", "context_version", "iat", "exp"]},
    )
    if any(
        not isinstance(claims[k], str) or not claims[k] for k in ("sub", "tenant_id", "run_id", "request_id")
    ):
        raise jwt.InvalidTokenError("Invalid identity claims")
    if type(claims["context_version"]) is not int or claims["context_version"] < 1:
        raise jwt.InvalidTokenError("Invalid context version")
    return claims


async def publish_context(
    conn,
    *,
    run_id,
    request_id,
    context_version,
    subject_id,
    tenant_id,
    operator_code,
    model_key,
    release_id,
    mapping_revision,
    locked_filters=None,
    index_keys=None,
    seconds=3600,
):
    """Backend integration API, NOT MCP. Caller must already verify aircraft and mappings.

    Call within the Backend request transaction. Existing run snapshots are immutable.
    Unchanged conditions may start another run at the same context version.
    Changed conditions require a newer version and revoke older context versions.
    """
    if type(context_version) is not int or context_version < 1 or seconds <= 0:
        raise ValueError("Invalid context version or lifetime")
    if not all(
        isinstance(v, str) and v for v in (run_id, request_id, subject_id, tenant_id, mapping_revision)
    ):
        raise ValueError("Nonempty identity and mapping revision required")
    filters = (
        locked_filters
        if locked_filters is not None
        else {"jasc_codes": [], "difficulty_from": None, "difficulty_to": None}
    )
    if set(filters) != {"jasc_codes", "difficulty_from", "difficulty_to"}:
        raise ValueError("Unknown or missing locked filter")
    codes = filters["jasc_codes"]
    if not isinstance(codes, list) or any(
        not isinstance(code, str) or len(code) != 4 or not code.isascii() or not code.isdigit()
        for code in codes
    ):
        raise ValueError("JASC must be four ASCII digits")
    lower, upper = (
        date.fromisoformat(filters[k]) if filters[k] is not None else None
        for k in ("difficulty_from", "difficulty_to")
    )
    if lower and upper and lower > upper:
        raise ValueError("Reversed difficulty date range")
    # Serialize publications for one request, including when no projection exists yet.
    await conn.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"context:{tenant_id}:{request_id}"},
    )
    previous = (
        (
            await conn.execute(
                select(db.contexts)
                .where(db.contexts.c.request_id == request_id, db.contexts.c.tenant_id == tenant_id)
                .order_by(db.contexts.c.context_version.desc())
                .limit(1)
            )
        )
        .mappings()
        .first()
    )
    if previous is not None:
        if context_version < previous["context_version"]:
            raise ValueError("Context version must increase; cannot restore an older version")
        if context_version == previous["context_version"]:
            conditions = {
                "operator_code": operator_code,
                "model_key": model_key,
                "mapping_revision": mapping_revision,
                "locked_filters": filters,
            }
            if any(previous[key] != value for key, value in conditions.items()):
                raise ValueError("Changed search conditions require a new context version")
    ready_release = await conn.scalar(
        select(db.releases.c.id).where(db.releases.c.id == release_id, db.releases.c.state == "READY")
    )
    if ready_release is None:
        raise ValueError("Release is not ready")
    rows = (
        (
            await conn.execute(
                select(db.indexes).where(db.indexes.c.release_id == release_id, db.indexes.c.state == "READY")
            )
        )
        .mappings()
        .all()
    )
    if index_keys is not None:
        if not isinstance(index_keys, list) or len(set(index_keys)) != len(index_keys):
            raise ValueError("Index keys must be a unique list")
        rows = [row for row in rows if row["index_key"] in index_keys]
        if {row["index_key"] for row in rows} != set(index_keys):
            raise ValueError("An index is unavailable in this release")
    methods = [(row["dataset"], row["method"]) for row in rows]
    if len(set(methods)) != len(methods):
        raise ValueError("Ambiguous indexes; supply explicit index_keys")
    indexes = sorted(row["index_key"] for row in rows)
    await conn.execute(
        update(db.contexts)
        .where(
            db.contexts.c.request_id == request_id,
            db.contexts.c.tenant_id == tenant_id,
            db.contexts.c.context_version != context_version,
        )
        .values(active=False)
    )
    await conn.execute(
        insert(db.contexts).values(
            run_id=run_id,
            request_id=request_id,
            context_version=context_version,
            subject_id=subject_id,
            tenant_id=tenant_id,
            operator_code=operator_code,
            model_key=model_key,
            release_id=release_id,
            index_ids=list(indexes),
            mapping_revision=mapping_revision,
            locked_filters=filters,
            active=True,
            expires_at=datetime.now(UTC) + timedelta(seconds=seconds),
        )
    )
