"""Exercise the production fusion/pagination code with conflicting, reproducible rank lists."""

import random
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import literal

from mro_mcp.common.errors import ToolFailure
from mro_mcp.rag.search import RagSearch


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows

    def mappings(self):
        return self


class RankedConnection:
    def __init__(self, lexical, semantic):
        self.rankings = iter((lexical, semantic))

    async def execute(self, statement):
        if statement._limit_clause is not None:
            return Rows([(i, str(i)) for i in next(self.rankings)[:statement._limit_clause.value]])
        return Rows([{"id": i} for i in range(200)])


async def test_fusion_pages_preserve_first_page_and_never_repeat_or_skip():
    rng = random.Random(42)
    lexical, semantic = list(range(200)), list(range(200))
    rng.shuffle(lexical)
    rng.shuffle(semantic)
    search = RagSearch(None, "test-cursor-secret-" * 3, SimpleNamespace(version="test"))
    search.scope = lambda ctx: []
    ctx = dict(run_id="run", subject_id="test", tenant_id="test", context_version=1,
               release_key="release", index_ids=[], operator_code=None, model_key="A320214",
               locked_filters={}, mapping_revision="test", expires_at=datetime.now(UTC) + timedelta(hours=1))
    args = {"query": "wheel", "limit": 20}
    seen = []
    first_cursor = None
    while True:
        rows, offset, bound, limited = await search.sdr_fused(
            RankedConnection(lexical, semantic), ctx, args, literal("wheel"), literal(0), literal(0), [1.0] * 1024
        )
        out = {"counts": {"scope_count": 200}, "next_cursor": None}
        page = search.finish_page("search_sdr_cases", ctx, bound, out, rows, offset)
        ids = [r["id"] for r in page]
        assert limited
        assert set(ids).isdisjoint(seen)
        if not seen:
            assert ids == [101, 76, 128, 2, 3, 153, 44, 73, 47, 32, 125, 66, 187, 94, 152, 163, 193, 111, 121, 92]
            first_cursor = out["next_cursor"]
        seen += ids
        if out["next_cursor"] is None:
            break
        assert len(seen) <= 100  # finite candidate window, including its final partial page
        args = {**args, "cursor": out["next_cursor"]}
    assert set(seen) == set(lexical[:50]) | set(semantic[:50])
    # Even with the same run and query, a changed ranking must not reuse a previous offset.
    with pytest.raises(ToolFailure, match="Cursor"):
        await search.sdr_fused(
            RankedConnection(semantic, semantic), ctx, {**args, "cursor": first_cursor},
            literal("wheel"), literal(0), literal(0), [1.0] * 1024,
        )
