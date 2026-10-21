"""B01~B03 result items (MCP spec v1.0 section 5, team interface v0.3 section 3)."""

from typing import Literal

from pydantic import AwareDatetime, Field

from ..common.models import AppliedScope, ItemResponse, ListResponse, Locator, LockedFilters, Model

# Spec v1.0 request states, plus CLOSED from team v0.3 (admin closure has no spec equivalent).
RequestStatus = Literal[
    "RECEIVED", "TRIAGING", "INVESTIGATING", "REVIEWING", "ASSIGNED", "ACCEPTED", "HUMAN_REVIEW", "CLOSED"
]


class Report(Model):
    symptom: str = Field(description="신고 증상 원문 (한국어 자연어)")
    reporter_role: Literal["pilot", "ground_maintenance", "other"]
    reported_at: AwareDatetime = Field(description="접수 시각 (UTC)")


class AircraftRef(Model):
    aircraft_id: str = Field(description="기체 식별자. 등록부호와 같다")
    registration: str = Field(description="등록부호 (예: HL7701)")
    model_raw: str | None = Field(description="접수에 입력된 정확한 모델 (예: A320-232)")


class RequestContext(Model):
    report: Report
    aircraft: AircraftRef
    resolved_scope: AppliedScope = Field(description="rag-mcp 검색에 쓸 범위. model_key는 하이픈을 뺀 모델")
    context_version: int = Field(ge=1, description="검색 범위 버전. 다른 도구에 그대로 넘긴다")
    request_version: int = Field(ge=1, description="업무 상태 버전")
    status: RequestStatus
    missing_fields: list[str] = Field(description="기체 마스터와 충돌하거나 비어 있는 접수 항목")
    locked_filters: LockedFilters


class HistoryItem(Model):
    evidence_id: str = Field(description="근거 ID. Evidence.ref_id에 그대로 쓴다 (MH-xxxxxx)")
    evidence_group_id: str
    source_type: Literal["mock_aircraft_history"] = "mock_aircraft_history"
    source_id: str
    source_version: str
    locator: Locator
    quote: str | None = Field(description="인용 가능한 원문 (신고 증상)")
    history_id: str = Field(description="수리이력 ID (MH-xxxxxx)")
    aircraft_id: str
    symptom: str
    observations: list[str] = Field(description="분류 정보. ata_chapter=21, jasc_code=2150 형태")
    actions: list[str] = Field(description="기록된 조치 내용")
    outcome: str | None = Field(description="open 또는 closed_at=<UTC 시각>. 조치 성공 여부는 기록되지 않음")
    department_id: str | None = Field(description="처리 부서 코드")
    occurred_at: AwareDatetime | None
    mock: Literal[True] = True
    source_sdr_ids: list[str] = []


class Department(Model):
    department_id: str = Field(
        description="부서 코드. 배정 제안에는 이 값만 쓴다 (AVI·ELE·ENG·HYD·SYS·STR·CAB)"
    )
    name: str
    responsibilities: list[str] = Field(description="담당 ATA 장 (예: ATA 29)")
    supported_models: list[str] = Field(description="담당 기종. 비어 있으면 모든 기종")
    scope_constraints: list[str]
    availability_status: Literal["available", "unavailable", "unknown"]
    mock: Literal[True] = True


RequestContextResponse = ItemResponse[RequestContext]
HistoryResponse = ListResponse[HistoryItem]
DepartmentResponse = ListResponse[Department]
