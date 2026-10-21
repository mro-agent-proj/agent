"""B01~B03 contracts from the shared catalog, adjusted to team interface v0.3."""

import copy
import json
from importlib.resources import files

from jsonschema import Draft202012Validator, FormatChecker

_CATALOG = json.loads(files("mro_rag").joinpath("tool_contracts.json").read_text())

# Team rule: the description is read by the LLM, so it says when to call the tool.
DESCRIPTIONS = {
    "get_request_context": (
        "접수 건의 증상·기체·현재 상태와 검색 범위(model_key)를 읽는다. "
        "조사를 시작할 때 가장 먼저 한 번 호출하고, 받은 context_version을 다른 도구에 그대로 넘긴다."
    ),
    "get_aircraft_history": (
        "같은 기체의 과거 수리이력(MH-*)을 최신순으로 읽는다. 반복 결함인지 판단할 때 쓴다. "
        "연결 여부는 도구가 판단하지 않으며, 근거로 쓸 때 history_id를 ref_id로 인용한다."
    ),
    "search_departments": (
        "배정 가능한 정비부서 후보와 담당 ATA를 읽는다. 부서를 제안하기 전에 호출하고, "
        "부서는 반환된 department_id(코드)로만 지칭한다. candidate_jasc는 순위 힌트일 뿐이다."
    ),
}

TOOLS = {}
for _tool in _CATALOG["tools"]:
    if _tool["id"] in {"B01", "B02", "B03"}:
        _mcp = copy.deepcopy(_tool["mcp"])
        _mcp["description"] = DESCRIPTIONS[_mcp["name"]]
        TOOLS[_mcp["name"]] = _mcp

# Team v0.3 has an admin-closed state; spec v1.0's enum has no equivalent value.
_status = TOOLS["get_request_context"]["outputSchema"]["properties"]["result"]["anyOf"][0]["properties"][
    "status"
]
_status["enum"] = [*_status["enum"], "CLOSED"]

INPUTS = {
    name: Draft202012Validator(t["inputSchema"], format_checker=FormatChecker()) for name, t in TOOLS.items()
}
OUTPUTS = {
    name: Draft202012Validator(t["outputSchema"], format_checker=FormatChecker()) for name, t in TOOLS.items()
}
