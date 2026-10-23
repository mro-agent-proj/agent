"""R01~R05 and the SDR -> AMM handoff lookup. Inputs are Pydantic-validated; search.py does the work."""

from typing import Annotated, Literal

from mcp.types import ToolAnnotations
from pydantic import Field, StringConstraints

from ..common.app import McpServer
from ..common.errors import ToolFailure
from ..common.models import JascCode, ScopeLevel
from .auth import principal
from .models import AmmResponse, EvidenceResponse, JascResponse, SdrAmmResponse, SdrResponse, TechResponse
from .search import RagSearch

READ_ONLY = ToolAnnotations(readOnlyHint=True)

RequestId = Annotated[
    str, Field(min_length=1, max_length=200, description="접수 ID. 위임 토큰의 request_id와 같아야 한다")
]
ContextVersion = Annotated[int, Field(ge=1, description="get_request_context가 돌려준 context_version")]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
Cursor = Annotated[str | None, Field(min_length=1, max_length=4096, description="이전 응답의 next_cursor")]
Limit = Annotated[int, Field(ge=1, le=20, description="반환 상한")]
SearchMode = Annotated[
    Literal["exact", "explore"],
    Field(description="exact: 구절 그대로 검색, explore: 넓게 검색. 둘 다 접수 범위·고정 필터는 유지"),
]
CandidateJasc = Annotated[
    list[JascCode], Field(max_length=10, description="추정 JASC 후보. 순위 가중치일 뿐 필터가 아니다")
]


def provided(**values):
    """The search layer distinguishes 'not given' from defaults, as the JSON contract did."""
    return {k: v for k, v in values.items() if v is not None and v != []}


def register(mcp: McpServer, search: RagSearch):
    async def run(name, args):
        return await search.run(name, args, principal.get())

    @mcp.tool(
        name="search_jasc_codes",
        title="자연어 증상에 대응할 수 있는 공식 JASC 코드 후보를 확인한다.",
        description=(
            "증상에 맞는 JASC 4자리 코드 후보를 찾거나(query), 코드의 공식 명칭·정의를 확인한다(codes). "
            "코드표는 영어다: query는 계통·부품 영어 단어로 쓴다(예: 'brake', 'flap control', 'exit sign light'). "
            "단어가 하나라도 맞으면 많이 맞는 순으로 돌려준다. 한국어 단어는 검색에 쓰이지 않는다. "
            "결과는 고장 원인 확정이나 부서 대응표가 아니라 검색 후보다."
        ),
        annotations=READ_ONLY,
    )
    async def search_jasc_codes(
        request_id: RequestId,
        context_version: ContextVersion,
        query: Annotated[
            Text | None, Field(description="코드 명칭·정의 영어 질의 (예: 'brake', 'flap control')")
        ] = None,
        codes: Annotated[
            list[JascCode] | None, Field(min_length=1, max_length=20, description="정확 조회할 코드")
        ] = None,
        limit: Limit = 5,
    ) -> JascResponse:
        if query is None and codes is None:
            raise ToolFailure("INVALID_ARGUMENT", "query or codes is required")
        args = provided(
            request_id=request_id, context_version=context_version, query=query, codes=codes, limit=limit
        )
        return JascResponse.model_validate(await run("search_jasc_codes", args))

    @mcp.tool(
        name="search_sdr_cases",
        title="현재 접수 범위 안에서 유사 증상·부품·조치가 기록된 SDR 사례를 찾는다.",
        description=(
            "Backend가 허용한 기종 범위에서 키워드·벡터 하이브리드 검색으로 SDR 사례를 찾는다. 기본은 동일 기본 모델·세대, FAMILY는 추가 조사용이다. "
            "SDR 원문은 영어다: query는 부품·현상·경고 메시지의 영어 핵심어 2~5개로 쓴다"
            "(예: 'brake vibration taxi', 'FLAP SYS 1 FAULT', 'exit sign light chime'). 한국어는 벡터 검색으로 보완한다. "
            "기본(explore)은 단어가 하나라도 맞으면 많이 맞는 순, search_mode='exact'는 구절 그대로 찾는다. "
            "NO_MATCH면 동의어로 바꾼다. "
            "reported_jasc는 확정 원인이 아니며, 근거로 쓸 때 evidence_id를 ref_id로 인용한다."
        ),
        annotations=READ_ONLY,
    )
    async def search_sdr_cases(
        request_id: RequestId,
        context_version: ContextVersion,
        query: Annotated[
            Text, Field(description="증상·부품·경고 메시지 영어 핵심어 2~5개 (예: 'brake vibration taxi')")
        ],
        scope_level: Annotated[ScopeLevel | None, Field(description="Backend 허용 범위: 기본 MODEL_GENERATION(동일 기본 모델·세대). FAMILY는 기본 범위 조사 후 추가 비교용. EXACT는 동일 세부 모델")] = None,
        candidate_jasc: CandidateJasc = [],  # noqa: B006 - pydantic copies defaults
        search_mode: SearchMode = "explore",
        limit: Limit = 5,
        cursor: Cursor = None,
    ) -> SdrResponse:
        args = provided(
            request_id=request_id,
            context_version=context_version,
            query=query,
            scope_level=scope_level,
            candidate_jasc=candidate_jasc,
            search_mode=search_mode,
            limit=limit,
            cursor=cursor,
        )
        return SdrResponse.model_validate(await run("search_sdr_cases", args))

    @mcp.tool(
        name="search_amm_references",
        title="증상과 관련된 과거 AMM 인용 맥락을 찾아 담당 부서에 전달할 참고번호를 제공한다.",
        description=(
            "AMM 번호(amm_reference_exact)로 그 번호가 인용된 SDR 문장과 인용 수준을 조회한다. "
            "의미 검색은 하지 않는다(AMM은 판단 근거가 아니라 배정 후 참고 정보). 근거로 쓴 SDR의 AMM 번호는 "
            "get_sdr_amm_references로 조회한다. 번호는 실제 매뉴얼 절차의 존재·적용성을 보장하지 않는다."
        ),
        annotations=READ_ONLY,
    )
    async def search_amm_references(
        request_id: RequestId,
        context_version: ContextVersion,
        amm_reference_exact: Annotated[
            str,
            StringConstraints(strip_whitespace=True, min_length=1, max_length=300),
            Field(description="정확 조회할 AMM 번호 (예: 32-11-00)"),
        ],
        reference_levels: Annotated[
            list[Literal["chapter_section", "subsection", "task_format"]],
            Field(max_length=3, description="인용 수준 필터"),
        ] = [],  # noqa: B006 - pydantic copies defaults
        limit: Limit = 5,
        cursor: Cursor = None,
    ) -> AmmResponse:
        args = provided(
            request_id=request_id,
            context_version=context_version,
            amm_reference_exact=amm_reference_exact,
            reference_levels=reference_levels,
            limit=limit,
            cursor=cursor,
        )
        return AmmResponse.model_validate(await run("search_amm_references", args))

    @mcp.tool(
        name="get_sdr_amm_references",
        title="근거로 쓴 SDR 사례에 인용된 AMM 참조번호와 인용 맥락을 조회한다.",
        description=(
            "판단에 쓴 SDR 근거(EV-SDR-*)마다, 그 신고서 원문에 인용된 AMM 번호와 인용 문장을 돌려준다. "
            "검색이 아니라 조회이며, 배정 후 정비팀에 참고번호로 전달하는 용도다. 부서 판단의 근거로 쓰지 않는다. "
            "AMM 번호는 실제 매뉴얼 절차의 존재·적용성을 보장하지 않는다. 접수 범위 밖 SDR은 결과에서 빠지고 경고로 알린다."
        ),
        annotations=READ_ONLY,
    )
    async def get_sdr_amm_references(
        request_id: RequestId,
        context_version: ContextVersion,
        evidence_ids: Annotated[
            list[Annotated[str, StringConstraints(pattern=r"^EV-SDR-[A-Za-z0-9-]{1,190}$")]],
            Field(min_length=1, max_length=10, description="판단에 쓴 SDR 근거 ID (EV-SDR-*)"),
        ],
    ) -> SdrAmmResponse:
        args = {
            "request_id": request_id,
            "context_version": context_version,
            "evidence_ids": sorted(set(evidence_ids)),
        }
        return SdrAmmResponse.model_validate(await run("get_sdr_amm_references", args))

    @mcp.tool(
        name="search_technical_docs",
        title="Airbus 공개 기술자료에서 계통 이해·추가 확인·부서 검토에 필요한 설명을 찾는다.",
        description=(
            "Airbus Safety First 정비 기사(공개 자료)에서 계통 동작·알려진 사례·점검 포인트 설명을 의미 검색(OpenAI 임베딩)으로 찾는다. "
            "질의 임베딩을 쓸 수 없으면 영어 단어 검색으로 대신하고 VECTOR_UNAVAILABLE 경고를 붙인다. "
            "query는 한국어·영어 모두 된다(예: 'dual bleed loss', '브레이크 교차 연결'). "
            "system_keywords(영어 계통·부품 단어)는 순위를 조금 올릴 뿐 필터가 아니다. "
            "접수 기종군(예: A320 계열)을 다루거나 Airbus 전 기종에 해당하는 기사만 돌려준다. "
            "기종이 명시되지 않은 문단(applicability_status=unknown)은 include_unknown_applicability=true일 때만 참고로 포함한다. "
            "기사는 실제 정비 절차나 현재 기체의 적용성 확인이 아니다. 근거로 쓸 때는 evidence_id(EV-DOC-*)를 그대로 인용한다."
        ),
        annotations=READ_ONLY,
    )
    async def search_technical_docs(
        request_id: RequestId,
        context_version: ContextVersion,
        query: Annotated[Text, Field(description="기술 설명 질의 (한국어·영어 의미 검색)")],
        system_keywords: Annotated[
            list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]],
            Field(max_length=20, description="계통·부품 영어 키워드 (예: 'bleed', 'brake'). 순위 가중치일 뿐 필터가 아니다"),
        ] = [],  # noqa: B006 - pydantic copies defaults
        include_unknown_applicability: Annotated[
            bool, Field(description="기종이 명시되지 않은 문단을 참고로 포함")
        ] = False,
        limit: Limit = 5,
        cursor: Cursor = None,
    ) -> TechResponse:
        args = provided(
            request_id=request_id,
            context_version=context_version,
            query=query,
            system_keywords=system_keywords,
            include_unknown_applicability=include_unknown_applicability,
            limit=limit,
            cursor=cursor,
        )
        return TechResponse.model_validate(await run("search_technical_docs", args))

    @mcp.tool(
        name="get_evidence",
        title="검색에서 받은 SDR·AMM 참조·기술자료 근거의 원문 범위를 다시 확인한다.",
        description=(
            "R02·R03·R04에서 받은 evidence_id의 원문과 인용 위치를 다시 확인한다. 배정 근거로 인용하기 전에 쓴다. "
            "수리이력(MH-*)은 business-mcp의 get_aircraft_history로 확인한다."
        ),
        annotations=READ_ONLY,
    )
    async def get_evidence(
        request_id: RequestId,
        context_version: ContextVersion,
        evidence_id: Annotated[
            str, Field(min_length=1, max_length=200, description="EV-SDR-*, EV-AMM-* 또는 EV-DOC-*")
        ],
    ) -> EvidenceResponse:
        args = {"request_id": request_id, "context_version": context_version, "evidence_id": evidence_id}
        return EvidenceResponse.model_validate(await run("get_evidence", args))
