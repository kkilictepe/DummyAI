"""``es_field_search`` — governed single-query Elasticsearch field search.

Collapses the reference 3-symbol proxy pattern (Request + Tool + ProxyTool, which existed only for
per-request registry state) into one async ``@tool`` over the single shared Elasticsearch client.
Unknown ``must_match`` filter names *and* unknown ``fields_to_return`` projection names are rejected
against the resolved profile's searchable fields *before* any query reaches Elasticsearch — so a
caller cannot exfiltrate ungoverned ``_source`` fields through the browser-visible result.
"""

from __future__ import annotations

import json
import time
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from src.clients import get_es_client
from src.clients.elasticsearch import ElasticsearchClient
from src.logging import get_logger
from src.tools.elasticsearch._common import internal_error, invalid_request, resolve_profile
from src.tools.elasticsearch.shared.response_governance import (
    apply_minimal_projection,
    build_response_meta,
    coerce_json_object_arg,
    fit_items_to_cap,
    validate_field_filters,
    validate_projection_fields,
)
from src.tools.elasticsearch.shared.time_range import parse_time_range

_log = get_logger(__name__)

_CAP = 256_000

_DESCRIPTION = (
    "Search Elasticsearch logs for a system_id over a time_range using structured field filters. "
    'Accepts must_match (JSON string of field->value pairs, e.g. \'{"host":"app01"}\'), '
    "optional text_search, log_level, exclude_patterns, sort_by, limit (max 1000), "
    "and fields_to_return. Returns {status, hits, response_meta} with compact matched log "
    "documents. Unknown must_match or fields_to_return field names return "
    "{status:'invalid_request'} without querying ES."
)


class FieldSearchRequest(BaseModel):
    """Input schema for ``es_field_search``."""

    system_id: str = Field(description="Target SAP system id, e.g. 'KHP'.")
    system_type: str | None = Field(
        default=None, description="Optional system_type override (e.g. 'SAP'); usually inferred."
    )
    time_range: str = Field(
        ...,
        description=(
            "Time window: a relative duration — '3h', '24h', '7d', '30m', '1w' (ending at now) — "
            "or an absolute ISO-8601 pair '<iso>/<iso>'."
        ),
    )
    must_match: str | None = Field(
        default=None,
        description=(
            "JSON object mapping field names to exact-match values "
            '(e.g. \'{"host":"app01","component":"syslog"}\'). Pass null or omit to match all.'
        ),
    )
    text_search: str | None = None
    log_level: str | None = None
    exclude_patterns: list[str] = Field(default_factory=list)
    sort_by: str = "@timestamp"
    limit: int = Field(default=100, ge=1, le=1000)
    fields_to_return: list[str] = Field(default_factory=list)


async def _run_field_search(
    es: ElasticsearchClient,
    *,
    system_id: str,
    time_range: str,
    system_type: str | None,
    must_match: str | None,
    text_search: str | None,
    log_level: str | None,
    exclude_patterns: list[str],
    sort_by: str,
    limit: int,
    fields_to_return: list[str],
) -> str:
    """Core: build the bool query, search, and govern the response. Takes an explicit client."""
    coerced = coerce_json_object_arg(must_match, "must_match")
    if isinstance(coerced, str):
        return invalid_request(
            "malformed_must_match",
            details=coerced.removeprefix("__parse_error__:"),
            system_id=system_id,
        )
    must_match_dict = coerced

    try:
        from_time, to_time = parse_time_range(time_range)
    except (ValueError, TypeError) as exc:
        return invalid_request(str(exc), system_id=system_id)

    profile = resolve_profile(system_id, system_type)
    field_err = validate_field_filters(must_match_dict, profile)
    if field_err is not None:
        return json.dumps(dict(field_err), default=str)

    # Govern the output projection too: fields_to_return is streamed to the browser, so a caller
    # may only project fields the profile knows about (searchable fields plus the baseline) — never
    # arbitrary _source fields. Reject unknown projections before any query reaches Elasticsearch.
    projection_err = validate_projection_fields(fields_to_return, profile)
    if projection_err is not None:
        return json.dumps(dict(projection_err), default=str)

    try:
        query_start = time.perf_counter()

        filter_clauses: list[dict[str, Any]] = [
            {"range": {"@timestamp": {"gte": from_time.isoformat(), "lte": to_time.isoformat()}}},
            {"term": {"system_id": system_id.upper()}},
        ]
        for field, value in must_match_dict.items():
            filter_clauses.append({"term": {field: value}})
        if log_level:
            filter_clauses.append({"term": {"log.level": log_level}})

        must_not_clauses: list[dict[str, Any]] = [
            {"wildcard": {"message": pat}} for pat in exclude_patterns
        ]
        must_clauses: list[dict[str, Any]] = []
        if text_search:
            must_clauses.append(
                {"query_string": {"query": text_search, "fields": ["message", "msg_text"]}}
            )

        body: dict[str, Any] = {
            "query": {
                "bool": {
                    "filter": filter_clauses,
                    "must_not": must_not_clauses,
                    "must": must_clauses,
                }
            },
            "_source": apply_minimal_projection(fields_to_return),
            "size": limit,
            "sort": [{sort_by: "asc"}],
            "track_total_hits": False,
        }

        raw = await es.search(index=es.default_index, body=body)
        query_time_ms = round((time.perf_counter() - query_start) * 1000, 2)

        raw_hits = (raw.get("hits") or {}).get("hits") or []
        hits = [h.get("_source", {}) for h in raw_hits]

        def _build(hits_slice: list[Any], truncated: bool, reason: str | None) -> str:
            meta = build_response_meta(
                hit_count=len(hits_slice),
                query_time_ms=query_time_ms,
                token_estimate=0,
                truncated=truncated,
                truncated_reason=reason,
            )
            payload = {"status": "ok", "hits": hits_slice, "response_meta": meta}
            meta["token_estimate"] = len(json.dumps(payload, default=str)) // 4
            return json.dumps(payload, default=str)

        result_str, _fitted, _truncated, _reason = fit_items_to_cap(hits, _build, cap=_CAP)
        return result_str

    except Exception:
        _log.exception("es_field_search_failed", system_id=system_id)
        return internal_error("Internal error while searching Elasticsearch.", system_id=system_id)


@tool("es_field_search", args_schema=FieldSearchRequest, description=_DESCRIPTION)
async def es_field_search(
    system_id: str,
    time_range: str,
    system_type: str | None = None,
    must_match: str | None = None,
    text_search: str | None = None,
    log_level: str | None = None,
    exclude_patterns: list[str] | None = None,
    sort_by: str = "@timestamp",
    limit: int = 100,
    fields_to_return: list[str] | None = None,
) -> str:
    """Search SAP logs with structured field filters (see the tool description)."""
    return await _run_field_search(
        get_es_client(),
        system_id=system_id,
        time_range=time_range,
        system_type=system_type,
        must_match=must_match,
        text_search=text_search,
        log_level=log_level,
        exclude_patterns=exclude_patterns or [],
        sort_by=sort_by,
        limit=limit,
        fields_to_return=fields_to_return or [],
    )
