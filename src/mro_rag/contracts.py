import json
from importlib.resources import files

from jsonschema import Draft202012Validator, FormatChecker

CATALOG = json.loads(files("mro_rag").joinpath("tool_contracts.json").read_text())
TOOLS = {t["mcp"]["name"]: t["mcp"] for t in CATALOG["tools"] if t["id"] in {"R01", "R02", "R03", "R05"}}
INPUTS = {
    name: Draft202012Validator(t["inputSchema"], format_checker=FormatChecker()) for name, t in TOOLS.items()
}
OUTPUTS = {
    name: Draft202012Validator(t["outputSchema"], format_checker=FormatChecker()) for name, t in TOOLS.items()
}
