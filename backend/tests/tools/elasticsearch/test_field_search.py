"""es_field_search: query construction, field governance, projection, and error masking."""

from __future__ import annotations

import json
from collections.abc import Callable

from src.tools.elasticsearch.tool_es_field_search import es_field_search

from ._helpers import FakeES, hit, search_response


async def test_happy_path_builds_bool_query(es_patch: Callable[[FakeES], FakeES]) -> None:
    fake = es_patch(
        FakeES([search_response([hit("1", {"message": "a"}), hit("2", {"message": "b"})])])
    )
    raw = await es_field_search.ainvoke(
        {"system_id": "khp", "time_range": "1h", "must_match": '{"host":"app01"}'}
    )
    result = json.loads(raw)
    assert result["status"] == "ok"
    assert len(result["hits"]) == 2

    index, body = fake.search_calls[0]
    assert index == fake.default_index
    filters = body["query"]["bool"]["filter"]
    assert {"term": {"system_id": "KHP"}} in filters  # system_id upper-cased into a term filter
    assert {"term": {"host": "app01"}} in filters
    assert any("range" in f for f in filters)
    assert "@timestamp" in body["_source"]  # baseline projection applied
    assert body["size"] == 100


async def test_unknown_field_rejected_without_search(es_patch: Callable[[FakeES], FakeES]) -> None:
    fake = es_patch(FakeES([]))
    raw = await es_field_search.ainvoke(
        {"system_id": "KHP", "time_range": "1h", "must_match": '{"bogus_field":"x"}'}
    )
    result = json.loads(raw)
    assert result["status"] == "invalid_request"
    assert "bogus_field" in result["reason"]
    assert fake.search_calls == []  # rejected before touching ES


async def test_requested_projection_fields_appear_in_source(
    es_patch: Callable[[FakeES], FakeES],
) -> None:
    # Known profile fields requested via fields_to_return are projected (on top of the baseline).
    fake = es_patch(FakeES([search_response([hit("1", {"message": "a", "host": "app01"})])]))
    await es_field_search.ainvoke(
        {"system_id": "KHP", "time_range": "1h", "fields_to_return": ["host", "user"]}
    )
    _index, body = fake.search_calls[0]
    assert "host" in body["_source"]
    assert "user" in body["_source"]
    assert "@timestamp" in body["_source"]  # baseline still present


async def test_unknown_projection_field_rejected_without_search(
    es_patch: Callable[[FakeES], FakeES],
) -> None:
    # fields_to_return is browser-visible, so an ungoverned field name (e.g. a would-be secret) must
    # be rejected as invalid_request BEFORE any ES query — mirroring must_match governance.
    fake = es_patch(FakeES([search_response([])]))
    raw = await es_field_search.ainvoke(
        {"system_id": "KHP", "time_range": "1h", "fields_to_return": ["password", "message"]}
    )
    result = json.loads(raw)
    assert result["status"] == "invalid_request"
    assert "password" in result["reason"]
    assert "message" not in result["reason"]  # baseline field is allowed, not reported as unknown
    assert fake.search_calls == []  # rejected before touching ES


async def test_malformed_must_match_is_invalid_request(
    es_patch: Callable[[FakeES], FakeES],
) -> None:
    fake = es_patch(FakeES([]))
    raw = await es_field_search.ainvoke(
        {"system_id": "KHP", "time_range": "1h", "must_match": "{not json"}
    )
    result = json.loads(raw)
    assert result["status"] == "invalid_request"
    assert result["reason"] == "malformed_must_match"
    assert fake.search_calls == []


async def test_bad_time_range_is_invalid_request(es_patch: Callable[[FakeES], FakeES]) -> None:
    fake = es_patch(FakeES([]))
    raw = await es_field_search.ainvoke({"system_id": "KHP", "time_range": "banana"})
    result = json.loads(raw)
    assert result["status"] == "invalid_request"
    assert fake.search_calls == []


async def test_exclude_patterns_and_text_search_in_body(
    es_patch: Callable[[FakeES], FakeES],
) -> None:
    fake = es_patch(FakeES([search_response([])]))
    await es_field_search.ainvoke(
        {
            "system_id": "KHP",
            "time_range": "1h",
            "text_search": "timeout",
            "log_level": "ERROR",
            "exclude_patterns": ["*healthcheck*"],
        }
    )
    _index, body = fake.search_calls[0]
    bool_q = body["query"]["bool"]
    assert {"wildcard": {"message": "*healthcheck*"}} in bool_q["must_not"]
    assert {"term": {"log.level": "ERROR"}} in bool_q["filter"]
    assert bool_q["must"][0]["query_string"]["query"] == "timeout"


async def test_exception_is_masked_and_json_safe(es_patch: Callable[[FakeES], FakeES]) -> None:
    # A raised exception carrying an internal hostname must not leak to the browser-visible result.
    es_patch(FakeES(raise_exc=RuntimeError("connect failed to es.internal:9200")))
    raw = await es_field_search.ainvoke({"system_id": "KHP", "time_range": "1h"})
    result = json.loads(raw)  # must be valid JSON
    assert result["status"] == "error"
    assert "es.internal" not in raw
    assert "9200" not in raw
