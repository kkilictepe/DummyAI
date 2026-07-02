"""Shared builders + a fake Elasticsearch client for the ES tool tests.

``FakeES`` duck-types the surface the tools touch (``default_index`` + async
``search``/``count``/``msearch``). It serves queued responses FIFO — a single fake can back a
multi-query tool (compare_windows runs two searches; drilldown up to three) — records every call
so tests can assert the request body, and can be told to raise (to exercise error masking).
"""

from __future__ import annotations

from typing import Any

_DEFAULT_SEARCH: dict[str, Any] = {"hits": {"total": {"value": 0}, "hits": []}}


class FakeES:
    """Minimal async Elasticsearch stand-in for the ES tools' ``_run_*`` cores."""

    def __init__(
        self,
        responses: list[dict[str, Any]] | None = None,
        *,
        default_index: str = "sap-logs-*",
        raise_exc: Exception | None = None,
    ) -> None:
        self.default_index = default_index
        self._responses = list(responses or [])
        self._raise = raise_exc
        self.search_calls: list[tuple[str, dict[str, Any]]] = []
        self.count_calls: list[tuple[str, dict[str, Any]]] = []
        self.msearch_calls: list[Any] = []

    def _next(self) -> dict[str, Any]:
        if self._responses:
            return self._responses.pop(0)
        return dict(_DEFAULT_SEARCH)

    async def search(self, index: str, body: dict[str, Any], **_kw: Any) -> dict[str, Any]:
        self.search_calls.append((index, body))
        if self._raise is not None:
            raise self._raise
        return self._next()

    async def count(self, index: str, body: dict[str, Any], **_kw: Any) -> dict[str, Any]:
        self.count_calls.append((index, body))
        if self._raise is not None:
            raise self._raise
        return self._next()

    async def msearch(self, body: Any, **_kw: Any) -> dict[str, Any]:
        self.msearch_calls.append(body)
        if self._raise is not None:
            raise self._raise
        return self._next()


# ---------------------------------------------------------------------------
# Response builders
# ---------------------------------------------------------------------------


def hit(
    doc_id: str, source: dict[str, Any], *, index: str = "sap-logs-2026.06.01"
) -> dict[str, Any]:
    """A single ES search hit."""
    return {"_id": doc_id, "_index": index, "_source": source}


def search_response(hits: list[dict[str, Any]], *, total: int | None = None) -> dict[str, Any]:
    """A search response wrapping *hits* with a total (defaults to len(hits))."""
    return {
        "hits": {
            "total": {"value": len(hits) if total is None else total},
            "hits": hits,
        }
    }


def terms_agg_response(
    buckets: list[dict[str, Any]], *, agg_name: str = "agg_result"
) -> dict[str, Any]:
    """A terms/date_histogram aggregation response."""
    return {"aggregations": {agg_name: {"buckets": buckets}}}


def cardinality_response(value: int) -> dict[str, Any]:
    return {"aggregations": {"agg_result": {"value": value}}}


def count_response(total: int) -> dict[str, Any]:
    return {"hits": {"total": {"value": total}}}


def compare_terms_response(
    buckets: list[dict[str, Any]], *, total: int | None = None, sum_other: int = 0
) -> dict[str, Any]:
    """A compare_windows terms response (top-level ``groups`` agg with ``sample`` sub-agg hits).

    ``total`` sets ``hits.total.value`` (real window doc count; defaults to the bucket-count sum);
    ``sum_other`` is the terms ``sum_other_doc_count`` (>0 means more distinct keys than top_n).
    """
    bucket_sum = sum(b["doc_count"] for b in buckets)
    return {
        "hits": {"total": {"value": bucket_sum if total is None else total}},
        "aggregations": {"groups": {"buckets": buckets, "sum_other_doc_count": sum_other}},
    }


def compare_bucket(key: str, count: int, sample_msg: str | None = None) -> dict[str, Any]:
    sample_hits = []
    if sample_msg is not None:
        sample_hits = [{"_source": {"message": sample_msg}}]
    return {"key": key, "doc_count": count, "sample": {"hits": {"hits": sample_hits}}}
