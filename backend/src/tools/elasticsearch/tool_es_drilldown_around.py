"""``es_drilldown_around`` — point-in-time context retrieval around an anchor log document.

One async ``@tool`` returning the documents immediately before and after an anchor (a known ES
document id or a raw ISO-8601 timestamp), each annotated with ``delta_ms`` from the anchor.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field, model_validator

from src.clients import get_es_client
from src.clients.elasticsearch import ElasticsearchClient
from src.logging import get_logger
from src.tools.elasticsearch._common import internal_error, invalid_request
from src.tools.elasticsearch.shared.response_governance import (
    apply_minimal_projection,
    build_response_meta,
    coerce_json_object_arg,
)
from src.tools.elasticsearch.shared.time_range import parse_iso_utc

_log = get_logger(__name__)

_CAP = 256_000

_DESCRIPTION = (
    "Retrieve log documents immediately before and after an anchor point within a configurable "
    "time window. The anchor is anchor_doc_id (a known ES document id) or a raw ISO-8601 UTC "
    "timestamp. Each matching document includes delta_ms — the absolute millisecond distance from "
    "the anchor. before_seconds/after_seconds define the window; limit_before/limit_after cap the "
    "result count per side (max 200 each). filter_must (JSON string of field->value pairs, e.g. "
    '\'{"host":"app01"}\') applies pre-filters without profile-based validation — unknown fields '
    "yield 0 hits rather than an invalid_request error. Returns {status, anchor, before, after, "
    "truncated_before, truncated_after, response_meta}. Missing anchor -> invalid_request; "
    "anchor_doc_id not found -> anchor_not_found."
)


class DrilldownAroundRequest(BaseModel):
    """Input schema for ``es_drilldown_around``."""

    system_id: str = Field(description="Target SAP system id, e.g. 'KHP'.")
    system_type: str | None = None
    anchor_doc_id: str | None = None
    timestamp: str | None = None
    before_seconds: int = Field(default=300, ge=1)
    after_seconds: int = Field(default=300, ge=1)
    limit_before: int = Field(default=25, ge=1, le=200)
    limit_after: int = Field(default=25, ge=1, le=200)
    filter_must: str | None = Field(
        default=None,
        description=(
            "JSON object mapping field names to exact-match values "
            '(e.g. \'{"host":"app01"}\'). Pass null or omit for no pre-filter.'
        ),
    )
    include_anchor: bool = True
    index_pattern: str | None = None

    @model_validator(mode="after")
    def _require_anchor(self) -> DrilldownAroundRequest:
        if self.anchor_doc_id is None and self.timestamp is None:
            raise ValueError("anchor_doc_id or timestamp required")
        return self


def _build_filter_clauses(filter_must: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not filter_must:
        return []
    return [{"term": {k: v}} for k, v in filter_must.items()]


async def _run_drilldown_around(
    es: ElasticsearchClient,
    *,
    system_id: str,
    anchor_doc_id: str | None,
    timestamp: str | None,
    before_seconds: int,
    after_seconds: int,
    limit_before: int,
    limit_after: int,
    filter_must: str | None,
    include_anchor: bool,
    index_pattern: str | None,
) -> str:
    """Core: resolve the anchor, fetch before/after windows, and govern the response."""
    # Guard: covers direct calls (the Pydantic validator covers the LangChain path).
    if anchor_doc_id is None and timestamp is None:
        return invalid_request(
            "anchor_doc_id or timestamp required",
            suggestion="provide anchor_doc_id (doc-id anchor) or timestamp (ISO-8601 anchor)",
        )

    coerced = coerce_json_object_arg(filter_must, "filter_must")
    if isinstance(coerced, str):
        return invalid_request(
            "malformed_filter_must",
            details=coerced.removeprefix("__parse_error__:"),
            system_id=system_id,
        )
    filter_must_dict = coerced

    # Parse a user-supplied timestamp anchor up front so a malformed value returns a clean
    # invalid_request (parse_iso_utc tolerates whitespace + a trailing 'Z'/'z') instead of being
    # swallowed by the broad handler below and surfacing as an opaque internal error.
    anchor_ts_from_input: datetime | None = None
    if anchor_doc_id is None and timestamp is not None:
        try:
            anchor_ts_from_input = parse_iso_utc(timestamp)
        except (ValueError, TypeError):
            return invalid_request(
                f"Invalid timestamp {timestamp!r}: expected an ISO-8601 UTC instant.",
                system_id=system_id,
            )

    try:
        query_start = time.perf_counter()
        default_idx = index_pattern or es.default_index
        # Every query is scoped to this system: the ~20 systems share one index, so system_id is
        # the ONLY thing keeping cross-system documents out of the anchor/before/after context.
        system_term: dict[str, Any] = {"term": {"system_id": system_id.upper()}}

        # Phase 1: resolve anchor timestamp and identity.
        if anchor_doc_id is not None:
            anchor_body: dict[str, Any] = {
                "query": {"bool": {"filter": [{"ids": {"values": [anchor_doc_id]}}, system_term]}},
                "size": 1,
                "_source": apply_minimal_projection([]),
            }
            anchor_raw = await es.search(index=default_idx, body=anchor_body)
            anchor_hits = (anchor_raw.get("hits") or {}).get("hits") or []
            if not anchor_hits:
                _log.warning(
                    "es_drilldown_anchor_not_found",
                    system_id=system_id,
                    anchor_doc_id=anchor_doc_id,
                )
                return json.dumps(
                    {
                        "status": "anchor_not_found",
                        "system_id": system_id,
                        "anchor_doc_id": anchor_doc_id,
                    },
                    default=str,
                )
            anchor_hit = anchor_hits[0]
            anchor_source = anchor_hit.get("_source", {})
            anchor_id: str | None = anchor_hit.get("_id")
            anchor_index = anchor_hit.get("_index") or default_idx
            anchor_ts = parse_iso_utc(anchor_source["@timestamp"])
        else:
            assert anchor_ts_from_input is not None  # guaranteed by the guard + pre-parse above
            anchor_ts = anchor_ts_from_input
            anchor_source = {"@timestamp": timestamp}
            anchor_id = None
            anchor_index = default_idx

        extra_filter = _build_filter_clauses(filter_must_dict)
        source_fields = apply_minimal_projection(list(filter_must_dict.keys()))

        # Phase 2a: BEFORE query (desc order -> reverse for chronological output).
        before_ts = anchor_ts - timedelta(seconds=before_seconds)
        before_body: dict[str, Any] = {
            "query": {
                "bool": {
                    "filter": [
                        {
                            "range": {
                                "@timestamp": {
                                    "gte": before_ts.isoformat(),
                                    "lt": anchor_ts.isoformat(),
                                }
                            }
                        },
                        system_term,
                        *extra_filter,
                    ]
                }
            },
            "size": limit_before,
            "sort": [{"@timestamp": {"order": "desc"}}],
            "_source": source_fields,
            "track_total_hits": False,
        }
        before_raw = await es.search(index=anchor_index, body=before_body)
        before_raw_hits = (before_raw.get("hits") or {}).get("hits") or []
        truncated_before = len(before_raw_hits) >= limit_before
        before_raw_hits = list(reversed(before_raw_hits))

        # Phase 2b: AFTER query (asc order, exclusive lower bound).
        after_ts = anchor_ts + timedelta(seconds=after_seconds)
        after_body: dict[str, Any] = {
            "query": {
                "bool": {
                    "filter": [
                        {
                            "range": {
                                "@timestamp": {
                                    "gt": anchor_ts.isoformat(),
                                    "lte": after_ts.isoformat(),
                                }
                            }
                        },
                        system_term,
                        *extra_filter,
                    ]
                }
            },
            "size": limit_after,
            "sort": [{"@timestamp": {"order": "asc"}}],
            "_source": source_fields,
            "track_total_hits": False,
        }
        after_raw = await es.search(index=anchor_index, body=after_body)
        after_raw_hits = (after_raw.get("hits") or {}).get("hits") or []
        truncated_after = len(after_raw_hits) >= limit_after

        def _project_hit(hit: dict[str, Any]) -> dict[str, Any]:
            src = hit.get("_source", {})
            ts_str = src.get("@timestamp", "")
            delta_ms: float | None
            try:
                doc_ts = parse_iso_utc(ts_str)
                delta_ms = abs((doc_ts - anchor_ts).total_seconds() * 1000)
            except (ValueError, AttributeError):
                delta_ms = None
            row: dict[str, Any] = {
                "@timestamp": ts_str,
                "delta_ms": delta_ms,
                "_id": hit.get("_id"),
            }
            row.update({k: v for k, v in src.items() if k != "@timestamp"})
            return row

        before_rows = [_project_hit(h) for h in before_raw_hits]
        after_rows = [_project_hit(h) for h in after_raw_hits]

        anchor_row: dict[str, Any] | None
        if include_anchor:
            anchor_row = {
                "@timestamp": anchor_source.get("@timestamp"),
                "delta_ms": 0,
                "_id": anchor_id,
            }
            anchor_row.update({k: v for k, v in anchor_source.items() if k != "@timestamp"})
        else:
            anchor_row = None

        query_time_ms = round((time.perf_counter() - query_start) * 1000, 2)
        anchor_count = 1 if include_anchor else 0
        fixed_truncation_parts: list[str] = []
        if truncated_before:
            fixed_truncation_parts.append(f"before window capped at {limit_before} docs")
        if truncated_after:
            fixed_truncation_parts.append(f"after window capped at {limit_after} docs")

        def _assemble_reason(byte_truncated: bool) -> str | None:
            parts = list(fixed_truncation_parts)
            if byte_truncated:
                parts.append(f"payload exceeded {_CAP} bytes; rows reduced to fit cap")
            return "; ".join(parts) if parts else None

        def _build_with_counts(before_n: int, after_n: int, byte_truncated: bool) -> str:
            before_slice = before_rows[-before_n:] if before_n else []
            after_slice = after_rows[:after_n]
            meta = build_response_meta(
                hit_count=len(before_slice) + len(after_slice) + anchor_count,
                query_time_ms=query_time_ms,
                token_estimate=0,
                truncated=bool(truncated_before or truncated_after or byte_truncated),
                truncated_reason=_assemble_reason(byte_truncated),
            )
            payload = {
                "status": "ok",
                "anchor": anchor_row,
                "before": before_slice,
                "after": after_slice,
                "truncated_before": truncated_before,
                "truncated_after": truncated_after,
                "response_meta": meta,
            }
            meta["token_estimate"] = len(json.dumps(payload, default=str)) // 4
            return json.dumps(payload, default=str)

        result_str = _build_with_counts(len(before_rows), len(after_rows), False)
        if len(result_str.encode("utf-8")) > _CAP:
            # Phase 1: binary-search the largest after-count that fits.
            lo, hi = 0, len(after_rows)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if len(_build_with_counts(len(before_rows), mid, True).encode("utf-8")) <= _CAP:
                    lo = mid
                else:
                    hi = mid - 1
            fitted_after = lo
            # Phase 2: if zero after still doesn't fit, reduce before-count.
            if (
                fitted_after == 0
                and len(_build_with_counts(len(before_rows), 0, True).encode("utf-8")) > _CAP
            ):
                lo2, hi2 = 0, len(before_rows)
                while lo2 < hi2:
                    mid2 = (lo2 + hi2 + 1) // 2
                    if len(_build_with_counts(mid2, 0, True).encode("utf-8")) <= _CAP:
                        lo2 = mid2
                    else:
                        hi2 = mid2 - 1
                result_str = _build_with_counts(lo2, 0, True)
            else:
                result_str = _build_with_counts(len(before_rows), fitted_after, True)

        return result_str

    except Exception:
        _log.exception("es_drilldown_around_failed", system_id=system_id)
        return internal_error(
            "Internal error while drilling down around the anchor.", system_id=system_id
        )


@tool("es_drilldown_around", args_schema=DrilldownAroundRequest, description=_DESCRIPTION)
async def es_drilldown_around(
    system_id: str,
    anchor_doc_id: str | None = None,
    timestamp: str | None = None,
    system_type: str | None = None,
    before_seconds: int = 300,
    after_seconds: int = 300,
    limit_before: int = 25,
    limit_after: int = 25,
    filter_must: str | None = None,
    include_anchor: bool = True,
    index_pattern: str | None = None,
) -> str:
    """Fetch log context immediately before/after an anchor document."""
    return await _run_drilldown_around(
        get_es_client(),
        system_id=system_id,
        anchor_doc_id=anchor_doc_id,
        timestamp=timestamp,
        before_seconds=before_seconds,
        after_seconds=after_seconds,
        limit_before=limit_before,
        limit_after=limit_after,
        filter_must=filter_must,
        include_anchor=include_anchor,
        index_pattern=index_pattern,
    )
