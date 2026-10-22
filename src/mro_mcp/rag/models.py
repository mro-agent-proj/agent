"""R01/R02/R03/R04/R05 result items (MCP spec v1.0 section 5)."""

from datetime import date
from typing import Literal

from pydantic import Field

from ..common.models import ItemResponse, JascCode, ListResponse, Locator, Model

ScoreKind = Literal["lexical", "vector", "fusion", "exact"]
ReferenceLevel = Literal["chapter_section", "subsection", "task_format"]


class JascItem(Model):
    code: JascCode
    title: str = Field(description="코드 명칭 (원문 그대로)")
    definition: str | None = Field(description="코드 정의. 없으면 null")
    source_url: str
    codebook_version: str


class EvidenceFields(Model):
    evidence_id: str = Field(description="근거 ID. Evidence.ref_id에 그대로 쓴다 (EV-SDR-* / EV-AMM-*)")
    evidence_group_id: str = Field(
        description="같은 원본 SDR에서 나온 근거 묶음. 묶음 하나는 독립 근거 하나로 센다"
    )
    source_id: str
    source_version: str
    quote: str | None = Field(description="원문 인용")
    score: float | None = Field(description="검색 순위 점수. 정답 확률이 아니다")
    score_kind: ScoreKind | None


class SdrItem(EvidenceFields):
    source_type: Literal["sdr"]
    source_locator: Locator
    case_id: str
    operator_code: str = Field(description="SDR 원문 운항사")
    model_key: str
    difficulty_date: date | None = Field(description="결함 발견일")
    submission_date: date | None
    reported_jasc: JascCode | None = Field(description="보고된 JASC. 확정 원인이 아니다")
    narrative_excerpt: str = Field(description="영어 원문 서술")
    component: str | None
    reported_actions: list[str] = Field(max_length=30)
    cited_references: list[str] = Field(max_length=30, description="이 사례에 인용된 정규화 AMM 번호")


class AmmItem(EvidenceFields):
    source_type: Literal["derived_amm_reference"]
    locator: Locator
    distilled_record_id: str
    source_case_id: str = Field(description="AMM 번호가 등장한 원본 SDR case_id")
    cited_amm_ref_raw: str
    cited_amm_ref_normalized: str = Field(description="정규화 AMM 번호. 여러 사례에 반복되므로 ID가 아니다")
    reference_level: ReferenceLevel
    symptom: str | None
    reported_action: str | None
    outcome: str | None
    relationship_status: Literal["explicit", "ambiguous", "unlinked"]
    applicability_status: Literal["not_verified"]
    reported_jasc: JascCode | None


class TechItem(EvidenceFields):
    source_type: Literal["airbus_technical_article"]
    locator: Locator
    document_id: str
    chunk_id: str
    title: str = Field(description="문서 제목")
    url: str = Field(description="원문 PDF 주소")
    section: str | None
    excerpt: str
    applicability_status: Literal["supported", "conditional", "unknown"] = Field(
        description="자료 적용 범위. unknown은 일반 적용 근거가 아니다"
    )
    applicability_basis: str | None = Field(description="적용 범위를 판단한 원문 문장")
    configuration_conditions: list[str] = Field(max_length=30, description="구성·개조 조건 원문")


class QuoteSpan(Model):
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    text: str


class EvidenceRecord(Model):
    evidence_id: str
    evidence_group_id: str
    source_type: Literal["sdr", "derived_amm_reference", "airbus_technical_article"]
    source_id: str
    source_version: str
    locator: Locator
    quote: str | None
    text: str = Field(description="원문 범위 텍스트")
    parent_source_ids: list[str] = Field(description="AMM 근거면 원본 SDR case_id")
    quote_spans: list[QuoteSpan]


JascResponse = ListResponse[JascItem]
SdrResponse = ListResponse[SdrItem]
AmmResponse = ListResponse[AmmItem]
TechResponse = ListResponse[TechItem]
EvidenceResponse = ItemResponse[EvidenceRecord]
