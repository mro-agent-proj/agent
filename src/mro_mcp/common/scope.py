"""Consume Backend-issued model allowances; never infer permissions from a model prefix."""

from pydantic import ValidationError

from .errors import ToolFailure
from .models import ModelScope


def model_policy(ctx):
    value = ctx.get("model_scope")
    if value is None:
        return None
    try:
        policy = ModelScope.model_validate(value).model_dump()
    except ValidationError as exc:
        raise ToolFailure("INVALID_SCOPE_POLICY", "Backend model allowance is invalid") from exc
    levels = policy["levels"]
    requested = ctx.get("model_key")
    if (levels.get("EXACT") != [requested] or policy["default_level"] not in levels
            or any(not keys or requested not in keys or len(keys) != len(set(keys)) for keys in levels.values())
            or not set(levels.get("MODEL_GENERATION", [])) <= set(levels.get("FAMILY", levels["EXACT"]))):
        raise ToolFailure("INVALID_SCOPE_POLICY", "Backend model allowance is inconsistent")
    return policy


def select_level(ctx, name, requested=None):
    policy = model_policy(ctx)
    default = policy["default_level"] if policy else "EXACT"
    if name == "search_sdr_cases":
        level = requested or default
    elif name in {"get_evidence", "get_sdr_amm_references"}:
        level = "FAMILY" if policy and "FAMILY" in policy["levels"] else default
    else:
        level = "EXACT"
    if level not in (policy["levels"] if policy else {"EXACT": [ctx.get("model_key")]}):
        raise ToolFailure("FORBIDDEN", "Search level is not authorized by this run")
    return level


def allowed_models(ctx):
    policy = model_policy(ctx)
    return policy["levels"][ctx.get("search_level", "EXACT")] if policy else [ctx["model_key"]]


def model_metadata(ctx, model):
    policy = model_policy(ctx)
    relation = "EXACT" if model == ctx["model_key"] else (
        "MODEL_GENERATION" if policy and model in policy["levels"].get("MODEL_GENERATION", []) else "FAMILY"
    )
    return {"model_key": model, "requested_model_key": ctx["model_key"], "model_relation": relation,
            "configuration_match": "UNVERIFIED"}
