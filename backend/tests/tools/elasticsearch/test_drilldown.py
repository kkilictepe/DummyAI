"""es_drilldown_around: anchor resolution, delta_ms, window ranges, not-found + guard."""

from __future__ import annotations

import json
from collections.abc import Callable

from src.tools.elasticsearch.tool_es_drilldown_around import (
    _run_drilldown_around,
    es_drilldown_around,
)

from ._helpers import FakeES, hit, search_response


async def test_timestamp_anchor_computes_delta_ms(es_patch: Callable[[FakeES], FakeES]) -> None:
    before = search_response(
        [hit("b1", {"@timestamp": "2026-06-01T09:59:30Z", "message": "before"})]
    )
    after = search_response([hit("a1", {"@timestamp": "2026-06-01T10:00:30Z", "message": "after"})])
    fake = es_patch(FakeES([before, after]))

    raw = await es_drilldown_around.ainvoke(
        {"system_id": "KHP", "timestamp": "2026-06-01T10:00:00Z"}
    )
    result = json.loads(raw)
    assert result["status"] == "ok"
    assert result["anchor"]["delta_ms"] == 0
    assert result["before"][0]["delta_ms"] == 30000.0
    assert result["after"][0]["delta_ms"] == 30000.0

    # Timestamp anchor performs no anchor lookup: exactly the before + after searches.
    assert len(fake.search_calls) == 2
    before_range = fake.search_calls[0][1]["query"]["bool"]["filter"][0]["range"]["@timestamp"]
    after_range = fake.search_calls[1][1]["query"]["bool"]["filter"][0]["range"]["@timestamp"]
    assert "lt" in before_range and "gte" in before_range
    assert "gt" in after_range and "lte" in after_range


async def test_doc_id_anchor_resolves_then_windows(es_patch: Callable[[FakeES], FakeES]) -> None:
    anchor = search_response(
        [hit("X", {"@timestamp": "2026-06-01T10:00:00Z", "message": "anchor"})]
    )
    before = search_response([])
    after = search_response([hit("a1", {"@timestamp": "2026-06-01T10:00:05Z", "message": "after"})])
    fake = es_patch(FakeES([anchor, before, after]))

    raw = await es_drilldown_around.ainvoke({"system_id": "KHP", "anchor_doc_id": "X"})
    result = json.loads(raw)
    assert result["status"] == "ok"
    assert result["anchor"]["_id"] == "X"
    assert len(fake.search_calls) == 3  # anchor lookup + before + after


async def test_doc_id_not_found(es_patch: Callable[[FakeES], FakeES]) -> None:
    fake = es_patch(FakeES([search_response([])]))
    raw = await es_drilldown_around.ainvoke({"system_id": "KHP", "anchor_doc_id": "missing"})
    result = json.loads(raw)
    assert result["status"] == "anchor_not_found"
    assert result["anchor_doc_id"] == "missing"
    assert len(fake.search_calls) == 1  # only the anchor lookup ran


async def test_missing_anchor_guard_direct_call() -> None:
    # The Pydantic validator blocks this via the LangChain path; the runtime guard covers
    # direct core calls.
    raw = await _run_drilldown_around(
        FakeES([]),
        system_id="KHP",
        anchor_doc_id=None,
        timestamp=None,
        before_seconds=300,
        after_seconds=300,
        limit_before=25,
        limit_after=25,
        filter_must=None,
        include_anchor=True,
        index_pattern=None,
    )
    result = json.loads(raw)
    assert result["status"] == "invalid_request"


async def test_exclude_anchor_from_output(es_patch: Callable[[FakeES], FakeES]) -> None:
    fake = es_patch(FakeES([search_response([]), search_response([])]))
    raw = await es_drilldown_around.ainvoke(
        {"system_id": "KHP", "timestamp": "2026-06-01T10:00:00Z", "include_anchor": False}
    )
    result = json.loads(raw)
    assert result["anchor"] is None
    assert len(fake.search_calls) == 2


async def test_before_after_queries_scope_by_system_id(
    es_patch: Callable[[FakeES], FakeES],
) -> None:
    # Regression: all context queries must scope by system_id (shared index -> otherwise the
    # before/after window returns every system's logs). Lowercase id must be upper-cased.
    fake = es_patch(FakeES([search_response([]), search_response([])]))
    await es_drilldown_around.ainvoke({"system_id": "khp", "timestamp": "2026-06-01T10:00:00Z"})
    assert len(fake.search_calls) == 2
    for _index, body in fake.search_calls:
        assert {"term": {"system_id": "KHP"}} in body["query"]["bool"]["filter"]


async def test_doc_id_anchor_query_scoped_by_system_id(
    es_patch: Callable[[FakeES], FakeES],
) -> None:
    anchor = search_response([hit("X", {"@timestamp": "2026-06-01T10:00:00Z"})])
    fake = es_patch(FakeES([anchor, search_response([]), search_response([])]))
    await es_drilldown_around.ainvoke({"system_id": "KHP", "anchor_doc_id": "X"})
    anchor_filter = fake.search_calls[0][1]["query"]["bool"]["filter"]
    assert {"term": {"system_id": "KHP"}} in anchor_filter


async def test_truncated_flags_at_limit_boundary(es_patch: Callable[[FakeES], FakeES]) -> None:
    # limit_before default 25: returning exactly 25 hits must flag truncated_before (>= boundary).
    before = search_response(
        [hit(f"b{i}", {"@timestamp": "2026-06-01T09:59:30Z"}) for i in range(25)]
    )
    es_patch(FakeES([before, search_response([])]))
    raw = await es_drilldown_around.ainvoke(
        {"system_id": "KHP", "timestamp": "2026-06-01T10:00:00Z"}
    )
    result = json.loads(raw)
    assert result["truncated_before"] is True
    assert result["truncated_after"] is False
    assert "before window capped" in (result["response_meta"]["truncated_reason"] or "")


async def test_over_cap_two_phase_byte_reduction(es_patch: Callable[[FakeES], FakeES]) -> None:
    # A >256KB after-window forces the two-phase byte reducer; the payload must end up under cap
    # with truncated=True and consistent response_meta.
    big_after = search_response(
        [
            hit(f"a{i}", {"@timestamp": "2026-06-01T10:00:05Z", "pad": "z" * 1500})
            for i in range(200)
        ]
    )
    es_patch(FakeES([search_response([]), big_after]))
    raw = await es_drilldown_around.ainvoke(
        {"system_id": "KHP", "timestamp": "2026-06-01T10:00:00Z", "limit_after": 200}
    )
    result = json.loads(raw)
    assert len(raw.encode("utf-8")) <= 256_000
    assert result["response_meta"]["truncated"] is True
    assert len(result["after"]) < 200
