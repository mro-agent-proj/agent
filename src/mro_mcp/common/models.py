"""Response envelope shared by every tool (MCP spec v1.0, section 4)."""

from datetime import date
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

Status = Literal["OK", "NO_DATA", "NO_MATCH", "NEEDS_INPUT", "ERROR"]
JascCode = Annotated[str, Field(pattern=r"^[0-9]{4}$", description="JASC 4자리 코드")]


class Model(BaseModel):
    # Spec schemas use additionalProperties: false.
    model_config = ConfigDict(extra="forbid")


class ErrorInfo(Model):
    code: str = Field(description="프로젝트 오류 코드 (INVALID_ARGUMENT, NOT_FOUND 등)")
    message: str = Field(description="공개 가능한 오류 설명")
    retryable: bool = Field(description="같은 입력으로 자동 재시도해도 되는지")
    retry_after_ms: int | None = None


class Counts(Model):
    scope_count: int | None = Field(None, description="기본 권한·자료 범위 건수. 계산하지 않았으면 null")
    eligible_count: int | None = Field(None, description="필터 적용 후 건수. 계산하지 않았으면 null")
    returned_count: int = Field(0, description="이번 응답의 항목 수")


class LockedFilters(Model):
    jasc_codes: list[JascCode] = []
    difficulty_from: date | None = None
    difficulty_to: date | None = None


class AppliedScope(Model):
    operator_code: str | None = None
    model_key: str | None = None
    operator_filter_applied: bool = False
    model_filter_applied: bool = False
    locked_filters: LockedFilters = LockedFilters()


class Locator(Model):
    source_url: str | None = None
    file_id: str | None = None
    record_key: str | None = None
    section: str | None = None
    start_offset: int | None = None
    end_offset: int | None = None


class ResponseBase(Model):
    status: Status = "OK"
    request_id: str | None = None
    context_version: int | None = None
    trace_id: str = Field(default_factory=lambda: str(uuid4()))
    applied_scope: AppliedScope | None = None
    snapshot_ids: list[str] = []
    counts: Counts = Counts()
    next_cursor: str | None = None
    warnings: list[str] = []
    missing_fields: list[str] = []
    error: ErrorInfo | None = None


class ListResponse[T](ResponseBase):
    """Search tools: items is empty on error."""

    items: list[T] = []


class ItemResponse[T](ResponseBase):
    """Single-object tools: result is null on error."""

    result: T | None = None
