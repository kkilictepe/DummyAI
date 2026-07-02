"""``es_raw_query`` — raw Elasticsearch DSL escape-hatch with a safety envelope.

One async ``@tool`` executing a caller-supplied ES request body, guarded by a fail-fast safety
envelope (first policy hit wins):

  1. script_detected          — recursive ``script`` key detection
  2. bool_nesting_too_deep    — bool nesting depth > 3
  3. invalid_endpoint         — not in {_search, _count, _msearch}
  4. timeout_too_large        — parsed timeout > 15s or unparseable
  5. size_exceeds_hard_cap    — size > 500 (even with explicit_high_volume)
  6. high_volume_not_opted_in — size > 100 without explicit_high_volume=True
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, cast

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from src.clients import get_es_client
from src.clients.elasticsearch import ElasticsearchClient
from src.logging import get_logger
from src.tools.elasticsearch._common import internal_error
from src.tools.elasticsearch.shared.response_governance import (
    build_response_meta,
    fit_items_to_cap,
)

_log = get_logger(__name__)

_CAP = 256_000
_ALLOWED_ENDPOINTS = frozenset({"_search", "_count", "_msearch"})
_TIMEOUT_RE = re.compile(r"^(\d+(?:\.\d+)?)(ms|s|m|h|d)?$")
_TIMEOUT_MULTIPLIERS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}

_DESCRIPTION = (
    "Execute a raw Elasticsearch DSL query against a system_id. Accepts query_dsl (JSON string — "
    "full ES request body; object for _search/_count, array for _msearch), endpoint "
    "(_search|_count|_msearch), index_override (optional), and explicit_high_volume (bool, default "
    "False). Subject to a strict safety envelope: no script queries, bool nesting <=3, timeout "
    "<=15s, size <=100 (or <=500 with explicit_high_volume). Returns {status:'ok', "
    "result:<es_response>, response_meta} on success, or {status:'rejected', policy, details} on a "
    "policy violation. This is a power-user escape hatch — prefer es_field_search / es_aggregation "
    "for ordinary queries."
)


def _contains_script(obj: Any) -> bool:
    """Return True if any dict key equals 'script' or starts with 'script'."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == "script" or key.startswith("script"):
                return True
            if _contains_script(value):
                return True
    elif isinstance(obj, list):
        return any(_contains_script(item) for item in obj)
    return False


def _bool_nesting_depth(obj: Any, current: int = 0) -> int:
    """Count nested bool query wrappers. Depth 1/2/3 pass; >=4 rejects."""
    if isinstance(obj, list):
        return max((_bool_nesting_depth(x, current) for x in obj), default=current)
    if not isinstance(obj, dict):
        return current
    mx = current
    for key, value in obj.items():
        next_depth = current + 1 if key == "bool" else current
        mx = max(mx, _bool_nesting_depth(value, next_depth))
    return mx


def _parse_timeout_seconds(v: Any) -> float | None:
    """Parse an ES timeout string ('10s', '500ms', '1m', ...) to seconds.

    Returns None if *v* is None, or for unparseable strings (so the caller treats them as invalid).
    """
    if v is None:
        return None
    m = _TIMEOUT_RE.match(str(v).strip().lower())
    if not m:
        return None
    unit = m.group(2) or "ms"
    return float(m.group(1)) * _TIMEOUT_MULTIPLIERS[unit]


def _rejection(policy: str, details: str) -> str:
    return json.dumps({"status": "rejected", "policy": policy, "details": details}, default=str)


def _over_cap_marker(hit_count: int, query_time_ms: float) -> str:
    """A governed, under-cap placeholder for an ES response too large to embed even after
    structural reduction (e.g. a multi-MB ``aggregations`` block)."""
    meta = build_response_meta(
        hit_count=hit_count,
        query_time_ms=query_time_ms,
        token_estimate=0,
        truncated=True,
        truncated_reason=f"ES response exceeded {_CAP} bytes and was omitted",
    )
    payload = {
        "status": "ok",
        "result": {"_omitted": True, "reason": "response exceeded the size cap"},
        "response_meta": meta,
    }
    meta["token_estimate"] = len(json.dumps(payload, default=str)) // 4
    return json.dumps(payload, default=str)


class RawQueryRequest(BaseModel):
    """Input schema for ``es_raw_query``."""

    system_id: str = Field(description="Target SAP system id, e.g. 'KHP'.")
    query_dsl: str = Field(
        ...,
        description=(
            "JSON-encoded Elasticsearch request body. For _search/_count pass a JSON object "
            '(e.g. \'{"query":{"match_all":{}},"size":10}\'); for _msearch pass a JSON array of '
            "header/body pairs."
        ),
    )
    endpoint: str = Field(default="_search", description="One of: _search, _count, _msearch")
    index_override: str | None = Field(
        default=None, description="Override the default index pattern"
    )
    explicit_high_volume: bool = Field(
        default=False,
        description="Set True to allow size 101-500. Size >500 is always rejected.",
    )


def _as_number(value: Any) -> float | None:
    """Return *value* as a number, or None if it is not numeric (bool is rejected)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _check_body_limits(body: dict[str, Any], explicit_high_volume: bool) -> str | None:
    """Enforce the per-body timeout + size limits. Returns a rejection JSON string or None."""
    raw_timeout = body.get("timeout")
    if raw_timeout is not None:
        parsed = _parse_timeout_seconds(raw_timeout)
        if parsed is None or parsed > 15.0:
            return _rejection(
                "timeout_too_large",
                f"Timeout '{raw_timeout}' exceeds the 15-second limit or could not be parsed.",
            )
    raw_size = body.get("size", 10)
    size = _as_number(raw_size)
    if size is None:
        return _rejection("malformed_query_dsl", f"size must be numeric, got {raw_size!r}.")
    if size > 500:
        return _rejection(
            "size_exceeds_hard_cap", f"Requested size={raw_size} exceeds the hard cap of 500."
        )
    if size > 100 and not explicit_high_volume:
        return _rejection(
            "high_volume_not_opted_in",
            f"Requested size={raw_size} exceeds 100. Set explicit_high_volume=True to allow up "
            "to 500.",
        )
    return None


def _check_safety_envelope(
    query_dsl: dict[str, Any] | list[Any],
    endpoint: str,
    explicit_high_volume: bool,
) -> str | None:
    """Run the fail-fast safety checks. Returns a rejection JSON string, or None to proceed."""
    if _contains_script(query_dsl):
        return _rejection(
            "script_detected",
            "Query contains 'script' or 'script_*' keys which are not permitted.",
        )
    if _bool_nesting_depth(query_dsl) > 3:
        return _rejection(
            "bool_nesting_too_deep", "Bool query nesting depth exceeds the maximum of 3."
        )
    if endpoint not in _ALLOWED_ENDPOINTS:
        return _rejection(
            "invalid_endpoint",
            f"Endpoint '{endpoint}' is not allowed. Use one of: _search, _count, _msearch.",
        )
    # Size/timeout limits apply to a _search/_count body (a dict) AND to every sub-body of an
    # _msearch array (a list) — otherwise an _msearch pair with size:5000 would bypass the cap.
    bodies = [query_dsl] if isinstance(query_dsl, dict) else query_dsl
    for body in bodies:
        if isinstance(body, dict):
            rejection = _check_body_limits(body, explicit_high_volume)
            if rejection is not None:
                return rejection
    return None


async def _run_raw_query(
    es: ElasticsearchClient,
    *,
    system_id: str,
    query_dsl: str | dict[str, Any] | list[Any],
    endpoint: str,
    index_override: str | None,
    explicit_high_volume: bool,
) -> str:
    """Core: parse + guard the DSL, execute, and govern the response."""
    try:
        query_start = time.perf_counter()

        # Coerce JSON-string input (LLM path). Tests may pass a native dict/list.
        if isinstance(query_dsl, str):
            try:
                parsed_dsl = json.loads(query_dsl)
            except json.JSONDecodeError as exc:
                return _rejection("malformed_query_dsl", f"query_dsl must be valid JSON: {exc}")
            if not isinstance(parsed_dsl, dict | list):
                return _rejection(
                    "malformed_query_dsl", "query_dsl JSON must decode to an object or array."
                )
            query_dsl = parsed_dsl

        rejection = _check_safety_envelope(query_dsl, endpoint, explicit_high_volume)
        if rejection is not None:
            return rejection

        index_name = index_override or es.default_index

        if endpoint == "_search":
            raw = await es.search(index=index_name, body=cast("dict[str, Any]", query_dsl))
            hit_count = len((raw.get("hits") or {}).get("hits") or [])
        elif endpoint == "_count":
            raw = await es.count(index=index_name, body=cast("dict[str, Any]", query_dsl))
            hit_count = raw.get("count", 0)
        else:  # _msearch
            raw = await es.msearch(body=cast("list[dict[str, Any]]", query_dsl))
            hit_count = sum(
                len((r.get("hits") or {}).get("hits") or []) for r in (raw.get("responses") or [])
            )

        query_time_ms = round((time.perf_counter() - query_start) * 1000, 2)

        def _envelope(result_obj: Any, truncated: bool, reason: str | None) -> str:
            meta = build_response_meta(
                hit_count=hit_count,
                query_time_ms=query_time_ms,
                token_estimate=0,
                truncated=truncated,
                truncated_reason=reason,
            )
            payload = {"status": "ok", "result": result_obj, "response_meta": meta}
            meta["token_estimate"] = len(json.dumps(payload, default=str)) // 4
            return json.dumps(payload, default=str)

        result_str = _envelope(raw, False, None)
        if len(result_str.encode("utf-8")) <= _CAP:
            return result_str

        # Over cap: structurally reduce the primary hits/responses list.
        if endpoint == "_search":
            hits_list = (raw.get("hits") or {}).get("hits") or []

            def _build_search(hits_slice: list[Any], truncated: bool, reason: str | None) -> str:
                new_hits = dict(raw.get("hits") or {})
                new_hits["hits"] = hits_slice
                return _envelope({**raw, "hits": new_hits}, truncated, reason)

            result_str, _f, _t, _r = fit_items_to_cap(hits_list, _build_search, cap=_CAP)
        elif endpoint == "_msearch":
            responses = raw.get("responses") or []

            def _build_msearch(resp_slice: list[Any], truncated: bool, reason: str | None) -> str:
                return _envelope({**raw, "responses": resp_slice}, truncated, reason)

            result_str, _f, _t, _r = fit_items_to_cap(responses, _build_msearch, cap=_CAP)

        # Final backstop: structural reduction cannot shrink non-list content (a large
        # `aggregations` block, or a _count body's `_shards`). If the payload is still over the cap,
        # return a governed marker so the browser-visible result stays valid JSON and under _CAP.
        if len(result_str.encode("utf-8")) > _CAP:
            result_str = _over_cap_marker(hit_count, query_time_ms)
        return result_str

    except Exception:
        _log.exception("es_raw_query_failed", system_id=system_id)
        return internal_error(
            "Internal error while executing the raw Elasticsearch query.", system_id=system_id
        )


@tool("es_raw_query", args_schema=RawQueryRequest, description=_DESCRIPTION)
async def es_raw_query(
    system_id: str,
    query_dsl: str,
    endpoint: str = "_search",
    index_override: str | None = None,
    explicit_high_volume: bool = False,
) -> str:
    """Execute a raw ES DSL query behind the safety envelope (power-user escape hatch)."""
    return await _run_raw_query(
        get_es_client(),
        system_id=system_id,
        query_dsl=query_dsl,
        endpoint=endpoint,
        index_override=index_override,
        explicit_high_volume=explicit_high_volume,
    )
