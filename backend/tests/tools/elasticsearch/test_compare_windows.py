"""es_compare_windows: window parsing, signature diff, terms diff, sorting, governance."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime

from src.tools.elasticsearch.tool_es_compare_windows import _parse_windows, es_compare_windows

from ._helpers import FakeES, compare_bucket, compare_terms_response, hit, search_response

_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)


def test_parse_windows_shift_and_now() -> None:
    (a_start, a_end), (b_start, b_end) = _parse_windows("shift:1h", "now", now=_NOW)
    assert b_end == _NOW
    assert b_start == _NOW.replace(hour=11)
    assert a_end == b_start
    assert a_start == b_start.replace(hour=10)


def test_parse_windows_relative_a_ends_at_b_start() -> None:
    (a_start, a_end), (b_start, b_end) = _parse_windows("2h", "1h", now=_NOW)
    assert b_end == _NOW
    assert a_end == b_start
    assert (a_end - a_start).total_seconds() == 2 * 3600


def test_parse_windows_iso_pair() -> None:
    (_a_start, a_end), (b_start, _b_end) = _parse_windows(
        "2026-05-01T00:00:00Z/2026-05-01T01:00:00Z",
        "2026-05-01T01:00:00Z/2026-05-01T02:00:00Z",
    )
    assert a_end == datetime(2026, 5, 1, 1, 0, 0, tzinfo=UTC)
    assert b_start == datetime(2026, 5, 1, 1, 0, 0, tzinfo=UTC)


async def test_signature_diff_flags_new_in_b_first(es_patch: Callable[[FakeES], FakeES]) -> None:
    window_a = search_response([hit("a1", {"message": "old error"}) for _ in range(2)])
    window_b = search_response(
        [hit("b1", {"message": "old error"}) for _ in range(2)]
        + [hit("b2", {"message": "brand new failure"}) for _ in range(3)]
    )
    fake = es_patch(FakeES([window_a, window_b]))
    raw = await es_compare_windows.ainvoke(
        {"system_id": "KHP", "window_a": "1h", "window_b": "1h", "group_by": "signature"}
    )
    result = json.loads(raw)
    assert result["status"] == "ok"
    # The new-in-B signature (count_a=0, count_b=3) is flagged and ranked first.
    top = result["groups"][0]
    assert top["is_new_in_b"] is True
    assert top["count_a"] == 0
    assert top["count_b"] == 3
    assert result["summary"]["new_groups_count"] >= 1
    assert len(fake.search_calls) == 2


async def test_terms_group_by_uses_mapped_field(es_patch: Callable[[FakeES], FakeES]) -> None:
    win_a = compare_terms_response([compare_bucket("app01", 5, "msg a")])
    win_b = compare_terms_response(
        [compare_bucket("app01", 9, "msg b"), compare_bucket("app02", 4, "new")]
    )
    fake = es_patch(FakeES([win_a, win_b]))
    raw = await es_compare_windows.ainvoke(
        {"system_id": "KHP", "window_a": "shift:1h", "window_b": "now", "group_by": "host"}
    )
    result = json.loads(raw)
    assert result["status"] == "ok"
    # group_by=host maps to the host.name ES field in the terms agg.
    _index, body = fake.search_calls[0]
    assert body["aggs"]["groups"]["terms"]["field"] == "host.name"
    keys = {g["key"]: g for g in result["groups"]}
    assert keys["app02"]["is_new_in_b"] is True
    assert keys["app01"]["count_b"] == 9


async def test_bad_window_is_invalid_request(es_patch: Callable[[FakeES], FakeES]) -> None:
    fake = es_patch(FakeES([]))
    raw = await es_compare_windows.ainvoke(
        {"system_id": "KHP", "window_a": "bogus", "window_b": "now"}
    )
    assert json.loads(raw)["status"] == "invalid_request"
    assert fake.search_calls == []


async def test_unknown_filter_must_rejected(es_patch: Callable[[FakeES], FakeES]) -> None:
    fake = es_patch(FakeES([]))
    raw = await es_compare_windows.ainvoke(
        {"system_id": "KHP", "window_a": "1h", "window_b": "1h", "filter_must": '{"nope":"x"}'}
    )
    assert json.loads(raw)["status"] == "invalid_request"
    assert fake.search_calls == []


async def test_both_windows_scope_by_system_id(es_patch: Callable[[FakeES], FakeES]) -> None:
    fake = es_patch(FakeES([compare_terms_response([]), compare_terms_response([])]))
    await es_compare_windows.ainvoke(
        {"system_id": "khp", "window_a": "1h", "window_b": "1h", "group_by": "host"}
    )
    for _index, body in fake.search_calls:
        assert {"term": {"system_id": "KHP"}} in body["query"]["bool"]["filter"]


async def test_terms_totals_use_hits_total_not_bucket_sum(
    es_patch: Callable[[FakeES], FakeES],
) -> None:
    # window_b has more distinct hosts than top_n: total_b must come from hits.total (500), not
    # the sum of the single returned bucket (120), and partial_diff must disclose the truncation.
    win_a = compare_terms_response([compare_bucket("app01", 100, "a")], total=100, sum_other=0)
    win_b = compare_terms_response([compare_bucket("app01", 120, "b")], total=500, sum_other=380)
    es_patch(FakeES([win_a, win_b]))
    raw = await es_compare_windows.ainvoke(
        {
            "system_id": "KHP",
            "window_a": "shift:1h",
            "window_b": "now",
            "group_by": "host",
            "top_n": 1,
        }
    )
    summary = json.loads(raw)["summary"]
    assert summary["total_a"] == 100
    assert summary["total_b"] == 500
    assert summary["partial_diff"] is True


async def test_signature_partial_diff_when_over_fetch_cap(
    es_patch: Callable[[FakeES], FakeES],
) -> None:
    # window_a fetched fewer docs than hits.total -> counts are a sample, partial_diff disclosed.
    win_a = search_response([hit("a", {"message": "e"})], total=5000)
    win_b = search_response([hit("b", {"message": "e"})], total=3)
    es_patch(FakeES([win_a, win_b]))
    raw = await es_compare_windows.ainvoke(
        {"system_id": "KHP", "window_a": "1h", "window_b": "1h", "group_by": "signature"}
    )
    summary = json.loads(raw)["summary"]
    assert summary["total_a"] == 5000
    assert summary["partial_diff"] is True


async def test_reversed_iso_pair_rejected(es_patch: Callable[[FakeES], FakeES]) -> None:
    fake = es_patch(FakeES([]))
    raw = await es_compare_windows.ainvoke(
        {
            "system_id": "KHP",
            "window_a": "2026-05-01T02:00:00Z/2026-05-01T01:00:00Z",
            "window_b": "now",
        }
    )
    assert json.loads(raw)["status"] == "invalid_request"
    assert fake.search_calls == []
