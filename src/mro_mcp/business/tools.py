"""B01~B03 over the backend's Flyway V1/V2 tables. Tools only read; errors are raised as ToolFailure."""

import base64
import hashlib
import json
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated
from uuid import UUID

from mcp.types import ToolAnnotations
from pydantic import Field, StringConstraints
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from ..common.app import McpServer
from ..common.db import is_permission_denied
from ..common.errors import ToolFailure
from ..common.models import AppliedScope, Counts, JascCode, Locator, LockedFilters
from .auth import authorize
from .models import (
    AircraftRef,
    Department,
    DepartmentResponse,
    HistoryItem,
    HistoryResponse,
    Report,
    RequestContext,
    RequestContextResponse,
)

# repair_requests has no search-scope version yet (team v0.3 section 7); every request is version 1.
# Team v0.3 status -> spec v1.0 status.
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
READ_ONLY = ToolAnnotations(readOnlyHint=True)

RequestId = Annotated[
    UUID, Field(description="접수 ID (repair_requests UUID). get_request_context가 돌려준 값")
]
ContextVersion = Annotated[int, Field(ge=1, description="get_request_context가 돌려준 context_version")]
Query = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


def encode_cursor(payload):
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(value):
    try:
        return json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
    except ValueError as exc:
        raise ToolFailure("INVALID_ARGUMENT", "cursor is not a server-issued cursor") from exc


def history_filter_key(request_id, occurred_from, occurred_to, query):
    raw = json.dumps([str(request_id), str(occurred_from), str(occurred_to), query], ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def check_context_version(value, claims):
    """The run's context_version is signed into the delegated token by the backend (same as the run row)."""
    if value != claims["context_version"]:
        raise ToolFailure(
            "CONTEXT_VERSION_CONFLICT",
            f"context_version is {claims['context_version']}; call get_request_context again",
        )


def start_of_day(day):
    return datetime.combine(day, time.min, UTC)


async def load_request(conn, request_id):
    try:
        row = (
            await conn.execute(
                text(
                    "SELECT request_id AS id, tail_number, aircraft_model, airline_code, symptom_text, status, "
                    "created_at, version FROM agent_request_context WHERE request_id = :id"
                ),
                {"id": request_id},
            )
        ).one_or_none()
    except DBAPIError as exc:
        if is_permission_denied(exc):
            raise ToolFailure(
                "UPSTREAM_UNAVAILABLE",
                "mro_business_reader has no SELECT grant on agent_request_context (Backend V9)",
            ) from exc
        raise
    if row is None:
        raise ToolFailure("NOT_FOUND", "No repair request with this request_id")
    return row


async def load_run(conn, claims):
    """The search scope the backend registered for this run: the same row rag-mcp pins (Backend V9 view)."""
    row = (
        await conn.execute(
            text(
                "SELECT context_version, operator_code, model_key, locked_filters, active, expires_at "
                "FROM agent_run_context WHERE run_id = :run AND request_id = :rid"
            ),
            {"run": claims["run_id"], "rid": claims["request_id"]},
        )
    ).one_or_none()
    if row is None or not row.active or row.expires_at <= datetime.now(UTC):
        raise ToolFailure("FORBIDDEN", "Run is unavailable or revoked")
    if row.context_version != claims["context_version"]:
        raise ToolFailure("CONTEXT_VERSION_CONFLICT", "Request context version changed")
    return row


def history_item(row, source_version):
    observations = [f"ata_chapter={row.ata_chapter}"]
    if row.jasc_code:
        observations.append(f"jasc_code={row.jasc_code}")
    return HistoryItem(
        evidence_id=row.id,
        evidence_group_id=row.id,
        source_id=row.id,
        source_version=source_version,
        locator=Locator(
            file_id="maintenance_history",
            record_key=row.id,
            section="symptom_text",
            start_offset=0,
            end_offset=len(row.symptom_text),
        ),
        quote=row.symptom_text,
        history_id=row.id,
        aircraft_id=row.tail_number,
        symptom=row.symptom_text,
        observations=observations,
        actions=[row.action_taken] if row.action_taken else [],
        outcome=f"closed_at={row.closed_at.astimezone(UTC).isoformat().replace('+00:00', 'Z')}"
        if row.closed_at
        else "open",
        department_id=row.department_code,
        occurred_at=row.reported_at,
    )


def register(mcp: McpServer, source_version: str):
    db = mcp.database

    @mcp.tool(
        name="get_request_context",
        title="접수 내용과 검증된 검색 범위, 현재 업무 상태를 읽는다.",
        description=(
            "접수 건의 증상·기체·현재 상태와 검색 범위(model_key)를 읽는다. "
            "조사를 시작할 때 가장 먼저 한 번 호출하고, 받은 context_version을 다른 도구에 그대로 넘긴다."
        ),
        annotations=READ_ONLY,
    )
    async def get_request_context(request_id: RequestId) -> RequestContextResponse:
        claims = authorize(request_id)
        async with db.read() as conn:
            request = await load_request(conn, request_id)
            run = await load_run(conn, claims)
        scope = AppliedScope(
            operator_code=run.operator_code,
            model_key=run.model_key,
            operator_filter_applied=run.operator_code is not None,
            model_filter_applied=run.model_key is not None,
            locked_filters=LockedFilters.model_validate(run.locked_filters or {}),
        )
        missing = [] if run.model_key else ["model_key"]
        warnings = []
        if run.operator_code is None:
            warnings.append("operator_code is null: search scope is pinned by model only (demo policy, team C02)")
        if missing:
            warnings.append("The run has no verified model mapping; RAG searches will ask for input")
        return RequestContextResponse(
            status="NEEDS_INPUT" if missing else "OK",
            request_id=str(request_id),
            context_version=run.context_version,
            counts=Counts(scope_count=1, eligible_count=1, returned_count=1),
            warnings=warnings,
            missing_fields=missing,
            result=RequestContext(
                report=Report(
                    symptom=request.symptom_text, reporter_role="other", reported_at=request.created_at
                ),
                aircraft=AircraftRef(
                    aircraft_id=request.tail_number,
                    registration=request.tail_number,
                    model_raw=request.aircraft_model,
                ),
                resolved_scope=scope,
                context_version=run.context_version,
                # JPA @Version starts at 0; the contract requires >= 1.
                request_version=request.version + 1,
                status=STATUS_MAP[request.status],
                missing_fields=missing,
                locked_filters=scope.locked_filters,
            ),
        )

    @mcp.tool(
        name="get_aircraft_history",
        title="동일 기체의 이전 신고·점검·조치 이력을 조회한다.",
        description=(
            "같은 기체의 과거 수리이력(MH-*)을 최신순으로 읽는다. 반복 결함인지 판단할 때 쓴다. "
            "연결 여부는 도구가 판단하지 않으며, 근거로 쓸 때 history_id를 ref_id로 인용한다."
        ),
        annotations=READ_ONLY,
    )
    async def get_aircraft_history(
        request_id: RequestId,
        context_version: ContextVersion,
        query: Annotated[
            Query | None, Field(description="참고용 증상 표현. 필터·정렬에는 쓰지 않는다")
        ] = None,
        occurred_from: Annotated[date | None, Field(description="이 날짜(UTC) 이후 이력만")] = None,
        occurred_to: Annotated[date | None, Field(description="이 날짜(UTC)까지의 이력만")] = None,
        limit: Annotated[int, Field(ge=1, le=100, description="반환 상한")] = 20,
        cursor: Annotated[
            str | None, Field(min_length=1, max_length=4096, description="이전 응답의 next_cursor")
        ] = None,
    ) -> HistoryResponse:
        claims = authorize(request_id)
        check_context_version(context_version, claims)
        filter_key = history_filter_key(request_id, occurred_from, occurred_to, query)
        async with db.read() as conn:
            request = await load_request(conn, request_id)
            clauses = ["tail_number = :tail", "reported_at < :before"]
            params = {"tail": request.tail_number, "before": request.created_at}
            scope_count = await conn.scalar(
                text(f"SELECT count(*) FROM maintenance_history WHERE {' AND '.join(clauses)}"), params
            )
            if occurred_from:
                clauses.append("reported_at >= :from_day")
                params["from_day"] = start_of_day(occurred_from)
            if occurred_to:
                clauses.append("reported_at < :to_day")
                params["to_day"] = start_of_day(occurred_to + timedelta(days=1))
            eligible_count = await conn.scalar(
                text(f"SELECT count(*) FROM maintenance_history WHERE {' AND '.join(clauses)}"), params
            )
            if cursor:
                position = decode_cursor(cursor)
                if position.get("k") != filter_key:
                    raise ToolFailure("INVALID_ARGUMENT", "cursor belongs to a different request or filter")
                clauses.append("(reported_at, id) < (:c_at, :c_id)")
                params.update(c_at=datetime.fromisoformat(position["t"]), c_id=position["id"])
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
        page = rows[:limit]
        warnings = [
            f"Only history reported before this request ({request.created_at.isoformat()}) is included"
        ]
        if query:
            warnings.append("query does not filter or rank history; compare symptoms yourself")
        next_cursor = None
        if len(rows) > limit:
            next_cursor = encode_cursor(
                {"k": filter_key, "t": page[-1].reported_at.isoformat(), "id": page[-1].id}
            )
        return HistoryResponse(
            status="OK" if page else "NO_DATA" if scope_count == 0 else "NO_MATCH",
            request_id=str(request_id),
            context_version=claims["context_version"],
            snapshot_ids=[source_version],
            counts=Counts(scope_count=scope_count, eligible_count=eligible_count, returned_count=len(page)),
            next_cursor=next_cursor,
            warnings=warnings,
            items=[history_item(row, source_version) for row in page],
        )

    @mcp.tool(
        name="search_departments",
        title="접수 범위와 업무 내용에 맞는 정비부서 후보를 조회한다.",
        description=(
            "배정 가능한 정비부서 후보와 담당 ATA를 읽는다. 부서를 제안하기 전에 호출하고, "
            "부서는 반환된 department_id(코드)로만 지칭한다. candidate_jasc는 순위 힌트일 뿐이다."
        ),
        annotations=READ_ONLY,
    )
    async def search_departments(
        request_id: RequestId,
        context_version: ContextVersion,
        query: Annotated[
            Query | None, Field(description="업무·계통 질의. 부서명이 들어 있으면 순위를 올린다")
        ] = None,
        candidate_jasc: Annotated[
            list[JascCode],
            Field(max_length=10, description="추정 JASC 후보. 앞 두 자리(ATA)로 순위만 조정한다"),
        ] = [],  # noqa: B006 - pydantic copies defaults
        limit: Annotated[int, Field(ge=1, le=20, description="반환 상한")] = 5,
    ) -> DepartmentResponse:
        claims = authorize(request_id)
        check_context_version(context_version, claims)
        async with db.read() as conn:
            await load_request(conn, request_id)
            rows = (
                await conn.execute(
                    text("SELECT code, name, ata_chapters, active FROM departments ORDER BY code")
                )
            ).all()
        active = [row for row in rows if row.active]
        hint_atas = {code[:2] for code in candidate_jasc}

        def chapters(row):
            return [c.strip() for c in row.ata_chapters.split(",") if c.strip()]

        def score(row):
            return 2 * len(hint_atas & set(chapters(row))) + (1 if query and row.name in query else 0)

        ranked = sorted(active, key=lambda row: (-score(row), row.code))[:limit]
        warnings = ["supported_models is empty: every department handles every aircraft model"]
        if hint_atas:
            warnings.append(
                "candidate_jasc ranks departments by the code's first two digits (ATA); it is a hint"
            )
        if len(active) > limit:
            warnings.append(f"{len(active) - limit} active departments omitted; raise limit to see all")
        return DepartmentResponse(
            status="OK" if active else "NO_DATA",
            request_id=str(request_id),
            context_version=claims["context_version"],
            counts=Counts(scope_count=len(rows), eligible_count=len(active), returned_count=len(ranked)),
            warnings=warnings,
            items=[
                Department(
                    department_id=row.code,
                    name=row.name,
                    responsibilities=[f"ATA {c}" for c in chapters(row)],
                    supported_models=[],
                    scope_constraints=[],
                    availability_status="available",
                )
                for row in ranked
            ],
        )
