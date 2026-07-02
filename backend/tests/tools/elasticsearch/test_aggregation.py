"""es_aggregation: per-type body construction, bucket normalization, param + field governance."""

from __future__ import annotations

import json
from collections.abc import Callable

from src.tools.elasticsearch.tool_es_aggregation import es_aggregation

from ._helpers import (
    FakeES,
    cardinality_response,
    count_response,
    terms_agg_response,
)


async def test_terms_aggregation_buckets(es_patch: Callable[[FakeES], FakeES]) -> None:
    fake = es_patch(
        FakeES(
            [
                terms_agg_response(
                    [{"key": "ERROR", "doc_count": 12}, {"key": "WARN", "doc_count": 3}]
                )
            ]
        )
    )
    raw = await es_aggregation.ainvoke(
        {
            "system_id": "KHP",
            "time_range": "1h",
            "agg_type": "terms",
            "field": "log.level",
            "top_n": 5,
        }
    )
    result = json.loads(raw)
    assert result["status"] == "success"
    assert result["buckets"] == [
        {"key": "ERROR", "count": 12},
        {"key": "WARN", "count": 3},
    ]
    _index, body = fake.search_calls[0]
    assert body["aggs"]["agg_result"]["terms"] == {"field": "log.level", "size": 5}
    assert body["size"] == 0


async def test_date_histogram_uses_fixed_vs_calendar_interval(
    es_patch: Callable[[FakeES], FakeES],
) -> None:
    fake = es_patch(
        FakeES(
            [
                terms_agg_response(
                    [{"key_as_string": "2026-06-01T00:00:00Z", "key": 0, "doc_count": 4}]
                )
            ]
        )
    )
    raw = await es_aggregation.ainvoke(
        {
            "system_id": "KHP",
            "time_range": "1d",
            "agg_type": "date_histogram",
            "field": "@timestamp",
            "interval": "1h",
        }
    )
    result = json.loads(raw)
    assert result["buckets"][0] == {"key": "2026-06-01T00:00:00Z", "count": 4}
    _index, body = fake.search_calls[0]
    # Hours/days map to fixed_interval (any multiplier); only weeks use calendar_interval.
    assert "fixed_interval" in body["aggs"]["agg_result"]["date_histogram"]

    # A multi-unit day interval (7d) must route to fixed_interval — calendar_interval only
    # permits a multiplier of 1, so ES would reject 'calendar_interval: 7d'.
    fake2 = es_patch(FakeES([terms_agg_response([])]))
    await es_aggregation.ainvoke(
        {
            "system_id": "KHP",
            "time_range": "30d",
            "agg_type": "date_histogram",
            "field": "@timestamp",
            "interval": "7d",
        }
    )
    _i2, body2 = fake2.search_calls[0]
    assert body2["aggs"]["agg_result"]["date_histogram"]["fixed_interval"] == "7d"

    # '1w' is the only valid week interval and routes to calendar_interval.
    fake3 = es_patch(FakeES([terms_agg_response([])]))
    await es_aggregation.ainvoke(
        {
            "system_id": "KHP",
            "time_range": "30d",
            "agg_type": "date_histogram",
            "field": "@timestamp",
            "interval": "1w",
        }
    )
    _i3, body3 = fake3.search_calls[0]
    assert body3["aggs"]["agg_result"]["date_histogram"]["calendar_interval"] == "1w"


async def test_date_histogram_multi_week_interval_rejected(
    es_patch: Callable[[FakeES], FakeES],
) -> None:
    # '2w' is not expressible in ES (calendar_interval needs multiplier 1; fixed_interval has no
    # week unit), so it is rejected before any query rather than failing at ES.
    fake = es_patch(FakeES([]))
    raw = await es_aggregation.ainvoke(
        {
            "system_id": "KHP",
            "time_range": "60d",
            "agg_type": "date_histogram",
            "field": "@timestamp",
            "interval": "2w",
        }
    )
    result = json.loads(raw)
    assert result["status"] == "invalid_request"
    assert "1w" in result["suggestion"]
    assert fake.search_calls == []


async def test_count_and_cardinality(es_patch: Callable[[FakeES], FakeES]) -> None:
    fake = es_patch(FakeES([count_response(42)]))
    raw = await es_aggregation.ainvoke(
        {"system_id": "KHP", "time_range": "1h", "agg_type": "count"}
    )
    assert json.loads(raw)["buckets"] == [{"key": "total", "count": 42}]
    assert fake.search_calls[0][1]["track_total_hits"] is True

    es_patch(FakeES([cardinality_response(7)]))
    raw2 = await es_aggregation.ainvoke(
        {"system_id": "KHP", "time_range": "1h", "agg_type": "cardinality", "field": "host"}
    )
    assert json.loads(raw2)["buckets"] == [{"key": "unique_count", "count": 7}]


async def test_date_histogram_requires_interval(es_patch: Callable[[FakeES], FakeES]) -> None:
    fake = es_patch(FakeES([]))
    raw = await es_aggregation.ainvoke(
        {
            "system_id": "KHP",
            "time_range": "1d",
            "agg_type": "date_histogram",
            "field": "@timestamp",
        }
    )
    result = json.loads(raw)
    assert result["status"] == "invalid_request"
    assert "interval" in result["reason"]
    assert fake.search_calls == []


async def test_terms_requires_field(es_patch: Callable[[FakeES], FakeES]) -> None:
    fake = es_patch(FakeES([]))
    raw = await es_aggregation.ainvoke(
        {"system_id": "KHP", "time_range": "1h", "agg_type": "terms"}
    )
    assert json.loads(raw)["status"] == "invalid_request"
    assert fake.search_calls == []


async def test_bad_interval_format_rejected(es_patch: Callable[[FakeES], FakeES]) -> None:
    fake = es_patch(FakeES([]))
    raw = await es_aggregation.ainvoke(
        {
            "system_id": "KHP",
            "time_range": "1d",
            "agg_type": "date_histogram",
            "field": "@timestamp",
            "interval": "1y",
        }
    )
    assert json.loads(raw)["status"] == "invalid_request"
    assert fake.search_calls == []


async def test_unknown_filter_must_field_rejected(es_patch: Callable[[FakeES], FakeES]) -> None:
    fake = es_patch(FakeES([]))
    raw = await es_aggregation.ainvoke(
        {"system_id": "KHP", "time_range": "1h", "agg_type": "count", "filter_must": '{"nope":"x"}'}
    )
    assert json.loads(raw)["status"] == "invalid_request"
    assert fake.search_calls == []


async def test_aggregation_scopes_by_system_id(es_patch: Callable[[FakeES], FakeES]) -> None:
    fake = es_patch(FakeES([count_response(1)]))
    await es_aggregation.ainvoke({"system_id": "khp", "time_range": "1h", "agg_type": "count"})
    assert {"term": {"system_id": "KHP"}} in fake.search_calls[0][1]["query"]["bool"]["filter"]
