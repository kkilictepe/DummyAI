"""``es_aggregation`` — governed Elasticsearch aggregation queries.

One async ``@tool`` supporting date_histogram / terms / count / cardinality aggregations over a
system_id's log index. Unknown ``filter_must`` field names **and** the aggregation ``field`` are
rejected against the resolved profile before any query reaches ES, matching the ``es_field_search``
governance contract — a ``terms`` aggregation returns the distinct *values* of ``field`` to the
browser, so an ungoverned field would let a caller enumerate arbitrary indexed data.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from src.clients import get_es_client
from src.clients.elasticsearch import ElasticsearchClient
from src.logging import get_logger
from src.tools.elasticsearch._common import internal_error, invalid_request, resolve_profile
from src.tools.elasticsearch.shared.response_governance import (
    build_response_meta,
    coerce_json_object_arg,
    fit_items_to_cap,
    validate_field_filters,
    validate_projection_fields,
)
from src.tools.elasticsearch.shared.time_range import parse_time_range

_log = get_logger(__name__)

_CAP = 256_000
_INTERVAL_RE = re.compile(r"^\d+[mhdw]$")

_DESCRIPTION = (
    "Run Elasticsearch aggregations over a system_id's log index for a time_range. "
    "agg_type values: 'date_histogram' (requires field + interval like '1h','15m','1d','1w' — "
    "returns bucket counts); 'terms' (requires field — returns top top_n values with counts); "
    "'count' (no field — total matching document count); 'cardinality' (requires field — unique "
    'value count). Use filter_must (JSON string of field->value pairs, e.g. \'{"host":"app01"}\') '
    "to pre-filter. Returns {status:'success', agg_type, field, buckets, response_meta}. Unknown "
    "filter_must or aggregation field names return {status:'invalid_request'} without querying ES."
)


class AggregationRequest(BaseModel):
    """Input schema for ``es_aggregation``."""

    system_id: str = Field(description="Target SAP system id, e.g. 'KHP'.")
    system_type: str | None = None
    time_range: str = Field(
        ...,
        description=(
            "Time window: a relative duration — '3h', '24h', '7d', '30m', '1w' — or an absolute "
            "ISO-8601 pair '<iso>/<iso>'."
        ),
    )
    agg_type: Literal["date_histogram", "terms", "count", "cardinality"]
    field: str | None = None
    interval: str | None = None
    top_n: int = Field(default=10, ge=1, le=1000)
    filter_must: str | None = Field(
        default=None,
        description=(
            "JSON object mapping field names to exact-match values "
            '(e.g. \'{"host":"app01"}\'). Pass null or omit for no pre-filter.'
        ),
    )


def _validate_agg_params(agg_type: str, field: str | None, interval: str | None) -> str | None:
    """Return an ``invalid_request`` JSON string if agg params are inconsistent, else None."""
    if agg_type == "date_histogram":
        if not field:
            return invalid_request(
                "date_histogram requires 'field'",
                suggestion="Provide a field parameter, e.g. field='@timestamp'",
            )
        if not interval:
            return invalid_request(
                "date_histogram requires 'interval'",
                suggestion="Provide an interval like '1h', '15m', '1d', '1w'",
            )
        if not _INTERVAL_RE.match(interval):
            return invalid_request(
                f"interval '{interval}' is invalid — must match \\d+[mhdw]",
                suggestion="Use format like '1h', '30m', '1d', '1w'",
            )
        # Weeks map to Elasticsearch calendar_interval, which only permits a multiplier of 1.
        # Minutes/hours/days map to fixed_interval and accept any multiplier (e.g. 7d).
        if interval.endswith("w") and interval != "1w":
            return invalid_request(
                f"interval '{interval}' is invalid — weeks only support '1w'",
                suggestion="Use '1w', or express multiple weeks in days (e.g. '7d', '14d').",
            )
    elif agg_type in ("terms", "cardinality") and not field:
        return invalid_request(
            f"{agg_type} requires 'field'",
            suggestion="Provide a field parameter, e.g. field='host'",
        )
    return None


def _extract_buckets(agg_type: str, raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize the ES aggregation response into a flat ``[{key, count}]`` list."""
    aggs = raw.get("aggregations") or {}
    if agg_type == "date_histogram":
        raw_buckets = aggs.get("agg_result", {}).get("buckets", [])
        return [
            {"key": b.get("key_as_string", str(b.get("key"))), "count": b.get("doc_count", 0)}
            for b in raw_buckets
        ]
    if agg_type == "terms":
        raw_buckets = aggs.get("agg_result", {}).get("buckets", [])
        return [{"key": str(b["key"]), "count": b["doc_count"]} for b in raw_buckets]
    if agg_type == "count":
        total_block = (raw.get("hits") or {}).get("total", {})
        total = total_block.get("value", 0) if isinstance(total_block, dict) else int(total_block)
        return [{"key": "total", "count": total}]
    if agg_type == "cardinality":
        unique_count = aggs.get("agg_result", {}).get("value", 0)
        return [{"key": "unique_count", "count": unique_count}]
    return []


async def _run_aggregation(
    es: ElasticsearchClient,
    *,
    system_id: str,
    time_range: str,
    agg_type: str,
    system_type: str | None,
    field: str | None,
    interval: str | None,
    top_n: int,
    filter_must: str | None,
) -> str:
    """Core: validate, build the aggregation body, execute, and govern the response."""
    coerced = coerce_json_object_arg(filter_must, "filter_must")
    if isinstance(coerced, str):
        return invalid_request(
            "malformed_filter_must",
            details=coerced.removeprefix("__parse_error__:"),
            system_id=system_id,
        )
    filter_must_dict = coerced

    param_err = _validate_agg_params(agg_type, field, interval)
    if param_err is not None:
        return param_err

    try:
        from_time, to_time = parse_time_range(time_range)
    except (ValueError, TypeError) as exc:
        return invalid_request(str(exc), system_id=system_id)

    profile = resolve_profile(system_id, system_type)
    if filter_must_dict:
        field_err = validate_field_filters(filter_must_dict, profile)
        if field_err is not None:
            _log.warning("es_aggregation_invalid_field_filter", system_id=system_id)
            return json.dumps(dict(field_err), default=str)

    # Govern the aggregation `field` exactly like must_match / fields_to_return: a terms agg returns
    # that field's distinct VALUES to the browser (cardinality/date_histogram also read it), so it
    # must be a field the profile declares — never an arbitrary, possibly sensitive, indexed field.
    # ``if field`` (not ``is not None``) so an empty-string field on a count call — which ignores
    # field entirely — is treated as absent rather than rejected.
    if field:
        agg_field_err = validate_projection_fields([field], profile)
        if agg_field_err is not None:
            _log.warning("es_aggregation_invalid_agg_field", system_id=system_id, field=field)
            return json.dumps(dict(agg_field_err), default=str)

    try:
        query_start = time.perf_counter()

        filter_clauses: list[dict[str, Any]] = [
            {"range": {"@timestamp": {"gte": from_time.isoformat(), "lte": to_time.isoformat()}}},
            {"term": {"system_id": system_id.upper()}},
        ]
        for key, value in filter_must_dict.items():
            filter_clauses.append({"term": {key: value}})

        body: dict[str, Any] = {"query": {"bool": {"filter": filter_clauses}}, "size": 0}
        if agg_type == "date_histogram":
            # Weeks -> calendar_interval (validated to '1w'); m/h/d -> fixed_interval (any N).
            interval_key = (
                "calendar_interval" if (interval or "").endswith("w") else "fixed_interval"
            )
            body["aggs"] = {
                "agg_result": {"date_histogram": {"field": field, interval_key: interval}}
            }
        elif agg_type == "terms":
            body["aggs"] = {"agg_result": {"terms": {"field": field, "size": top_n}}}
        elif agg_type == "count":
            body["track_total_hits"] = True
        elif agg_type == "cardinality":
            body["aggs"] = {"agg_result": {"cardinality": {"field": field}}}

        raw = await es.search(index=es.default_index, body=body)
        query_time_ms = round((time.perf_counter() - query_start) * 1000, 2)

        buckets = _extract_buckets(agg_type, raw)

        def _build(buckets_slice: list[Any], truncated: bool, reason: str | None) -> str:
            meta = build_response_meta(
                hit_count=len(buckets_slice),
                query_time_ms=query_time_ms,
                token_estimate=0,
                truncated=truncated,
                truncated_reason=reason,
            )
            payload = {
                "status": "success",
                "agg_type": agg_type,
                "field": field,
                "buckets": buckets_slice,
                "response_meta": meta,
            }
            meta["token_estimate"] = len(json.dumps(payload, default=str)) // 4
            return json.dumps(payload, default=str)

        result_str, _fitted, _truncated, _reason = fit_items_to_cap(buckets, _build, cap=_CAP)
        return result_str

    except Exception:
        _log.exception("es_aggregation_failed", system_id=system_id)
        return internal_error(
            "Internal error while aggregating Elasticsearch logs.", system_id=system_id
        )


@tool("es_aggregation", args_schema=AggregationRequest, description=_DESCRIPTION)
async def es_aggregation(
    system_id: str,
    time_range: str,
    agg_type: str,
    system_type: str | None = None,
    field: str | None = None,
    interval: str | None = None,
    top_n: int = 10,
    filter_must: str | None = None,
) -> str:
    """Aggregate SAP logs (date_histogram / terms / count / cardinality)."""
    return await _run_aggregation(
        get_es_client(),
        system_id=system_id,
        time_range=time_range,
        agg_type=agg_type,
        system_type=system_type,
        field=field,
        interval=interval,
        top_n=top_n,
        filter_must=filter_must,
    )
