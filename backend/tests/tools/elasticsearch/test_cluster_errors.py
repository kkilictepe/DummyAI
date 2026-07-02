"""es_cluster_errors: fetch -> normalize -> cluster, ranking, min size, governance, empties."""

from __future__ import annotations

import json
from collections.abc import Callable

from src.tools.elasticsearch.tool_es_cluster_errors import es_cluster_errors

from ._helpers import FakeES, hit, search_response


def _log_hits() -> list[dict[str, object]]:
    hits: list[dict[str, object]] = []
    for i in range(3):
        hits.append(
            hit(
                f"c{i}",
                {
                    "message": "connection failed",
                    "system_id": "KHP",
                    "@timestamp": f"2026-06-01T10:00:0{i}Z",
                },
            )
        )
    for i in range(2):
        hits.append(
            hit(
                f"d{i}",
                {
                    "message": "disk full",
                    "system_id": "KHP",
                    "@timestamp": f"2026-06-01T10:00:1{i}Z",
                },
            )
        )
    return hits


async def test_two_ranked_clusters(es_patch: Callable[[FakeES], FakeES]) -> None:
    fake = es_patch(FakeES([search_response(_log_hits())]))
    raw = await es_cluster_errors.ainvoke(
        {"system_id": "KHP", "time_range": "1h", "min_cluster_size": 2}
    )
    result = json.loads(raw)
    assert result["status"] == "ok"
    assert result["total_docs_examined"] == 5
    clusters = result["clusters"]
    assert len(clusters) == 2
    assert clusters[0]["count"] == 3
    assert clusters[1]["count"] == 2
    assert clusters[0]["sample_message"] == "connection failed"
    # track_total_hits requested so total_docs_examined reflects the real match count.
    assert fake.search_calls[0][1]["track_total_hits"] is True


async def test_singleton_filtered_by_min_cluster_size(es_patch: Callable[[FakeES], FakeES]) -> None:
    hits = [
        *_log_hits(),
        hit("s0", {"message": "one off", "system_id": "KHP", "@timestamp": "2026-06-01T10:00:20Z"}),
    ]
    es_patch(FakeES([search_response(hits)]))
    raw = await es_cluster_errors.ainvoke(
        {"system_id": "KHP", "time_range": "1h", "min_cluster_size": 2}
    )
    result = json.loads(raw)
    assert len(result["clusters"]) == 2  # singleton dropped


async def test_empty_hits_returns_no_clusters(es_patch: Callable[[FakeES], FakeES]) -> None:
    es_patch(FakeES([search_response([])]))
    raw = await es_cluster_errors.ainvoke({"system_id": "KHP", "time_range": "1h"})
    result = json.loads(raw)
    assert result["status"] == "ok"
    assert result["clusters"] == []
    assert result["total_docs_examined"] == 0


async def test_unknown_must_match_rejected(es_patch: Callable[[FakeES], FakeES]) -> None:
    fake = es_patch(FakeES([]))
    raw = await es_cluster_errors.ainvoke(
        {"system_id": "KHP", "time_range": "1h", "must_match": '{"nope":"x"}'}
    )
    assert json.loads(raw)["status"] == "invalid_request"
    assert fake.search_calls == []


async def test_exception_is_masked(es_patch: Callable[[FakeES], FakeES]) -> None:
    es_patch(FakeES(raise_exc=RuntimeError("boom es.internal:9200")))
    raw = await es_cluster_errors.ainvoke({"system_id": "KHP", "time_range": "1h"})
    result = json.loads(raw)
    assert result["status"] == "error"
    assert "es.internal" not in raw


async def test_cluster_scopes_by_system_id(es_patch: Callable[[FakeES], FakeES]) -> None:
    fake = es_patch(FakeES([search_response([])]))
    await es_cluster_errors.ainvoke({"system_id": "khp", "time_range": "1h"})
    assert {"term": {"system_id": "KHP"}} in fake.search_calls[0][1]["query"]["bool"]["filter"]
