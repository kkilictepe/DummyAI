"""es_raw_query: safety-envelope policy order, endpoint dispatch, happy paths, error masking."""

from __future__ import annotations

import json
from collections.abc import Callable

from src.tools.elasticsearch.tool_es_raw_query import es_raw_query

from ._helpers import FakeES, count_response, hit, search_response


def _dsl(obj: object) -> str:
    return json.dumps(obj)


async def test_search_happy_path(es_patch: Callable[[FakeES], FakeES]) -> None:
    fake = es_patch(FakeES([search_response([hit("1", {"message": "a"})])]))
    raw = await es_raw_query.ainvoke(
        {"system_id": "KHP", "query_dsl": _dsl({"query": {"match_all": {}}, "size": 10})}
    )
    result = json.loads(raw)
    assert result["status"] == "ok"
    assert result["response_meta"]["hit_count"] == 1
    assert len(fake.search_calls) == 1


async def test_count_and_msearch_dispatch(es_patch: Callable[[FakeES], FakeES]) -> None:
    fake = es_patch(FakeES([count_response(9)]))
    raw = await es_raw_query.ainvoke(
        {"system_id": "KHP", "endpoint": "_count", "query_dsl": _dsl({"query": {"match_all": {}}})}
    )
    assert json.loads(raw)["status"] == "ok"
    assert len(fake.count_calls) == 1

    fake2 = es_patch(FakeES([{"responses": [search_response([hit("1", {})])]}]))
    raw2 = await es_raw_query.ainvoke(
        {
            "system_id": "KHP",
            "endpoint": "_msearch",
            "query_dsl": _dsl([{"index": "x"}, {"query": {}}]),
        }
    )
    assert json.loads(raw2)["status"] == "ok"
    assert len(fake2.msearch_calls) == 1


async def test_script_detected_rejected(es_patch: Callable[[FakeES], FakeES]) -> None:
    fake = es_patch(FakeES([]))
    raw = await es_raw_query.ainvoke(
        {"system_id": "KHP", "query_dsl": _dsl({"query": {"script": {"source": "1"}}})}
    )
    result = json.loads(raw)
    assert result["status"] == "rejected"
    assert result["policy"] == "script_detected"
    assert fake.search_calls == []


async def test_bool_nesting_too_deep(es_patch: Callable[[FakeES], FakeES]) -> None:
    es_patch(FakeES([]))
    deep = {"query": {"bool": {"must": [{"bool": {"must": [{"bool": {"must": [{"bool": {}}]}}]}}]}}}
    raw = await es_raw_query.ainvoke({"system_id": "KHP", "query_dsl": _dsl(deep)})
    assert json.loads(raw)["policy"] == "bool_nesting_too_deep"


async def test_invalid_endpoint(es_patch: Callable[[FakeES], FakeES]) -> None:
    es_patch(FakeES([]))
    raw = await es_raw_query.ainvoke(
        {"system_id": "KHP", "endpoint": "_delete", "query_dsl": _dsl({"query": {"match_all": {}}})}
    )
    assert json.loads(raw)["policy"] == "invalid_endpoint"


async def test_timeout_too_large(es_patch: Callable[[FakeES], FakeES]) -> None:
    es_patch(FakeES([]))
    raw = await es_raw_query.ainvoke(
        {"system_id": "KHP", "query_dsl": _dsl({"query": {"match_all": {}}, "timeout": "30s"})}
    )
    assert json.loads(raw)["policy"] == "timeout_too_large"


async def test_size_hard_cap_and_high_volume_optin(es_patch: Callable[[FakeES], FakeES]) -> None:
    es_patch(FakeES([]))
    raw = await es_raw_query.ainvoke(
        {"system_id": "KHP", "query_dsl": _dsl({"query": {"match_all": {}}, "size": 600})}
    )
    assert json.loads(raw)["policy"] == "size_exceeds_hard_cap"

    es_patch(FakeES([]))
    raw2 = await es_raw_query.ainvoke(
        {"system_id": "KHP", "query_dsl": _dsl({"query": {"match_all": {}}, "size": 200})}
    )
    assert json.loads(raw2)["policy"] == "high_volume_not_opted_in"

    # Opting in allows 101-500 and the query executes.
    fake = es_patch(FakeES([search_response([])]))
    raw3 = await es_raw_query.ainvoke(
        {
            "system_id": "KHP",
            "query_dsl": _dsl({"query": {"match_all": {}}, "size": 200}),
            "explicit_high_volume": True,
        }
    )
    assert json.loads(raw3)["status"] == "ok"
    assert len(fake.search_calls) == 1


async def test_policy_order_script_beats_endpoint(es_patch: Callable[[FakeES], FakeES]) -> None:
    # A query that violates several policies is rejected by the earliest (script) check.
    es_patch(FakeES([]))
    raw = await es_raw_query.ainvoke(
        {
            "system_id": "KHP",
            "endpoint": "_delete",
            "query_dsl": _dsl({"query": {"script": {"source": "x"}}}),
        }
    )
    assert json.loads(raw)["policy"] == "script_detected"


async def test_malformed_json_rejected(es_patch: Callable[[FakeES], FakeES]) -> None:
    fake = es_patch(FakeES([]))
    raw = await es_raw_query.ainvoke({"system_id": "KHP", "query_dsl": "{not json"})
    assert json.loads(raw)["policy"] == "malformed_query_dsl"
    assert fake.search_calls == []


async def test_exception_is_masked(es_patch: Callable[[FakeES], FakeES]) -> None:
    es_patch(FakeES(raise_exc=RuntimeError("boom es.internal:9200")))
    raw = await es_raw_query.ainvoke(
        {"system_id": "KHP", "query_dsl": _dsl({"query": {"match_all": {}}})}
    )
    result = json.loads(raw)
    assert result["status"] == "error"
    assert "es.internal" not in raw


async def test_msearch_sub_body_size_is_enforced(es_patch: Callable[[FakeES], FakeES]) -> None:
    # Regression: the size/timeout envelope must apply to _msearch sub-bodies (a list), not only
    # to a dict body — a size:5000 pair previously bypassed the hard cap entirely.
    fake = es_patch(FakeES([]))
    body = [{"index": "x"}, {"query": {"match_all": {}}, "size": 5000}]
    raw = await es_raw_query.ainvoke(
        {"system_id": "KHP", "endpoint": "_msearch", "query_dsl": _dsl(body)}
    )
    assert json.loads(raw)["policy"] == "size_exceeds_hard_cap"
    assert fake.msearch_calls == []


async def test_non_numeric_size_rejected(es_patch: Callable[[FakeES], FakeES]) -> None:
    # A JSON string size (e.g. "600") must be rejected cleanly, not raise a TypeError that the
    # generic handler masks as an internal_error.
    fake = es_patch(FakeES([]))
    raw = await es_raw_query.ainvoke(
        {"system_id": "KHP", "query_dsl": _dsl({"query": {"match_all": {}}, "size": "600"})}
    )
    assert json.loads(raw)["policy"] == "malformed_query_dsl"
    assert fake.search_calls == []


async def test_unparseable_timeout_rejected(es_patch: Callable[[FakeES], FakeES]) -> None:
    fake = es_patch(FakeES([]))
    raw = await es_raw_query.ainvoke(
        {"system_id": "KHP", "query_dsl": _dsl({"query": {"match_all": {}}, "timeout": "soon"})}
    )
    assert json.loads(raw)["policy"] == "timeout_too_large"
    assert fake.search_calls == []


async def test_over_cap_hits_are_structurally_reduced(es_patch: Callable[[FakeES], FakeES]) -> None:
    big = search_response([hit(f"h{i}", {"message": "m", "pad": "z" * 400}) for i in range(2000)])
    es_patch(FakeES([big]))
    raw = await es_raw_query.ainvoke(
        {
            "system_id": "KHP",
            "query_dsl": _dsl({"query": {"match_all": {}}, "size": 500}),
            "explicit_high_volume": True,
        }
    )
    result = json.loads(raw)
    assert len(raw.encode("utf-8")) <= 256_000
    assert result["response_meta"]["truncated"] is True
    assert len(result["result"]["hits"]["hits"]) < 2000


async def test_over_cap_large_aggregations_return_marker(
    es_patch: Callable[[FakeES], FakeES],
) -> None:
    # A huge aggregations block (empty hits) cannot be structurally reduced; the backstop returns
    # a governed under-cap marker instead of shipping a >256KB browser payload.
    buckets = [{"key": f"host{i}", "doc_count": i, "pad": "y" * 200} for i in range(3000)]
    resp = {
        "hits": {"total": {"value": 0}, "hits": []},
        "aggregations": {"t": {"buckets": buckets}},
    }
    es_patch(FakeES([resp]))
    raw = await es_raw_query.ainvoke(
        {
            "system_id": "KHP",
            "query_dsl": _dsl({"size": 0, "aggs": {"t": {"terms": {"field": "host"}}}}),
        }
    )
    result = json.loads(raw)
    assert len(raw.encode("utf-8")) <= 256_000
    assert result["result"].get("_omitted") is True
    assert result["response_meta"]["truncated"] is True
