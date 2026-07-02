"""``es_compare_windows`` — diff two time-window aggregations from Elasticsearch.

One async ``@tool`` that compares error-category / signature distributions across two windows to
surface what is new, gone, or changed between them.
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, NamedTuple

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from src.clients import get_es_client
from src.clients.elasticsearch import ElasticsearchClient
from src.logging import get_logger
from src.tools.elasticsearch._common import internal_error, invalid_request, resolve_profile
from src.tools.elasticsearch.shared.profiles import LogInvestigationProfile
from src.tools.elasticsearch.shared.response_governance import (
    build_response_meta,
    coerce_json_object_arg,
    fit_items_to_cap,
    validate_field_filters,
)
from src.tools.elasticsearch.shared.signature_extractor import SignatureExtractor
from src.tools.elasticsearch.shared.time_range import parse_iso_utc, parse_relative_time_range

_log = get_logger(__name__)

_CAP = 256_000
_SIGNATURE_FETCH_CAP = 1000

_GROUP_BY_TO_ES_FIELD: dict[str, str] = {
    "error_code": "error_code",
    "component": "component",
    "host": "host.name",
    "log_level": "log.level",
}

_SHIFT_RE = re.compile(r"^(\d+)\s*([mhdw])$", re.IGNORECASE)
_RELATIVE_RE = re.compile(r"^\d+[mhdw]$", re.IGNORECASE)
_ISO_PAIR_RE = re.compile(r"^.+/.+$")

_DESCRIPTION = (
    "Diff two time-window aggregations from Elasticsearch to surface error changes. window_a and "
    "window_b define the two periods. Window formats: 'shift:Xunit' (window_a ends where window_b "
    "starts, shifted back by X); 'Nh'/'Nm'/'Nd'/'Nw'; '<iso>/<iso>'. window_b also accepts 'now' "
    "(last 1h). group_by: 'error_code','component','host','log_level' use a terms-agg with a "
    "sample_msg sub-agg; 'signature' (default) fetches raw docs and groups by signature hash. "
    "sort: 'new_in_b_first' (default), 'delta_desc', 'count_b_desc', 'count_a_desc'. top_n caps "
    "returned groups (1-100, default 20). Unknown filter_must fields return "
    "{status:'invalid_request'} without querying ES."
)


def _parse_shift_delta(shift_str: str) -> timedelta:
    """Parse a shift string like '1h', '30m', '2d' into a timedelta."""
    m = _SHIFT_RE.match(shift_str.strip())
    if not m:
        raise ValueError(f"Invalid shift format: {shift_str!r}")
    value = int(m.group(1))
    unit = m.group(2).lower()
    unit_map = {
        "m": timedelta(minutes=value),
        "h": timedelta(hours=value),
        "d": timedelta(days=value),
        "w": timedelta(weeks=value),
    }
    return unit_map[unit]


def _parse_iso_pair(pair: str, label: str) -> tuple[datetime, datetime]:
    """Parse '<iso>/<iso>' into (start, end) UTC datetimes."""
    parts = pair.split("/", 1)
    if len(parts) != 2:
        raise ValueError(f"{label}: expected '<iso>/<iso>', got {pair!r}")
    # Shared parser tolerates whitespace + a trailing 'Z'/'z' and treats naive datetimes as UTC.
    start = parse_iso_utc(parts[0])
    end = parse_iso_utc(parts[1])
    if start >= end:
        raise ValueError(f"{label}: ISO pair start must be strictly before end")
    return start, end


def _parse_windows(
    window_a: str,
    window_b: str,
    *,
    now: datetime | None = None,
) -> tuple[tuple[datetime, datetime], tuple[datetime, datetime]]:
    """Parse ``window_a`` and ``window_b`` into (start, end) UTC datetime pairs."""
    _now = now or datetime.now(UTC)

    # --- window_b first ---
    wb = window_b.strip().lower()
    if wb == "now":
        b_end = _now
        b_start = _now - timedelta(hours=1)
    elif _RELATIVE_RE.match(wb):
        b_start, b_end = parse_relative_time_range(window_b, now=_now)
    elif _ISO_PAIR_RE.match(window_b):
        try:
            b_start, b_end = _parse_iso_pair(window_b, "window_b")
        except (ValueError, TypeError) as exc:
            raise ValueError(f"window_b={window_b!r}: {exc}") from exc
    else:
        raise ValueError(
            f"window_b={window_b!r}: unrecognised format. "
            "Use 'now', 'Nh'/'Nm'/'Nd'/'Nw', or '<iso>/<iso>'."
        )

    # --- window_a ---
    wa = window_a.strip()
    if wa.lower().startswith("shift:"):
        shift_str = wa[6:].strip()
        try:
            shift_delta = _parse_shift_delta(shift_str)
        except ValueError as exc:
            raise ValueError(f"window_a={window_a!r}: {exc}") from exc
        a_end = b_start
        a_start = b_start - shift_delta
    elif _RELATIVE_RE.match(wa):
        a_start, _ = parse_relative_time_range(window_a, now=b_start)
        a_end = b_start
    elif _ISO_PAIR_RE.match(wa):
        try:
            a_start, a_end = _parse_iso_pair(window_a, "window_a")
        except (ValueError, TypeError) as exc:
            raise ValueError(f"window_a={window_a!r}: {exc}") from exc
    else:
        raise ValueError(
            f"window_a={window_a!r}: unrecognised format. "
            "Use 'shift:Xunit', 'Nh'/'Nm'/'Nd'/'Nw', or '<iso>/<iso>'."
        )

    for dt in (a_start, a_end, b_start, b_end):
        if dt.tzinfo is None:
            raise ValueError("Parsed datetime is timezone-naive; expected UTC-aware datetimes")
    return (a_start, a_end), (b_start, b_end)


def _build_filter_clauses(filter_must: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not filter_must:
        return []
    return [{"term": {k: v}} for k, v in filter_must.items()]


def _diff_bucket_sets(
    dict_a: dict[str, dict[str, Any]],
    dict_b: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Produce a per-key diff list from two window bucket dicts."""
    all_keys = set(dict_a) | set(dict_b)
    groups: list[dict[str, Any]] = []
    for key in all_keys:
        count_a = dict_a.get(key, {}).get("count", 0)
        count_b = dict_b.get(key, {}).get("count", 0)
        pct_change = ((count_b - count_a) / count_a * 100) if count_a > 0 else None
        groups.append(
            {
                "key": key,
                "count_a": count_a,
                "count_b": count_b,
                "delta": count_b - count_a,
                "pct_change": pct_change,
                "is_new_in_b": key in dict_b and key not in dict_a,
                "is_gone_in_b": key in dict_a and key not in dict_b,
                "sample_msg_a": dict_a.get(key, {}).get("sample_msg"),
                "sample_msg_b": dict_b.get(key, {}).get("sample_msg"),
            }
        )
    return groups


def _apply_sort(groups: list[dict[str, Any]], sort: str) -> None:
    """Sort groups in-place. Ties break on the key for deterministic ordering."""
    if sort == "new_in_b_first":
        groups.sort(key=lambda g: (not g["is_new_in_b"], -g["count_b"], g["key"]))
    elif sort == "delta_desc":
        groups.sort(key=lambda g: (-g["delta"], g["key"]))
    elif sort == "count_b_desc":
        groups.sort(key=lambda g: (-g["count_b"], g["key"]))
    elif sort == "count_a_desc":
        groups.sort(key=lambda g: (-g["count_a"], g["key"]))


class _Window(NamedTuple):
    """One window's diff inputs. ``total`` is the true window doc count (from hits.total);
    ``truncated`` means ``buckets`` is only a partial view (top_n terms, or the 1000-doc
    signature sample), so per-key diffs may be incomplete."""

    buckets: dict[str, dict[str, Any]]
    total: int
    truncated: bool


def _hits_total(raw: dict[str, Any], fallback: int) -> int:
    """Read ``hits.total.value`` (ES 7+ dict shape), tolerating the legacy int shape / absence."""
    total_block = (raw.get("hits") or {}).get("total")
    if isinstance(total_block, dict):
        return int(total_block.get("value", fallback))
    if isinstance(total_block, int):
        return total_block
    return fallback


async def _run_terms_agg_window(
    es: ElasticsearchClient,
    idx: str,
    system_id: str,
    group_by: str,
    filter_must: dict[str, Any] | None,
    start: datetime,
    end: datetime,
    top_n: int,
) -> _Window:
    """Run a terms-agg query for one window. total is the real window doc count; truncated when
    there are more distinct keys than top_n (sum_other_doc_count > 0)."""
    es_field = _GROUP_BY_TO_ES_FIELD[group_by]
    filter_clauses: list[dict[str, Any]] = [
        {"range": {"@timestamp": {"gte": start.isoformat(), "lte": end.isoformat()}}},
        {"term": {"system_id": system_id.upper()}},
        *_build_filter_clauses(filter_must),
    ]
    body: dict[str, Any] = {
        "size": 0,
        "track_total_hits": True,
        "query": {"bool": {"filter": filter_clauses}},
        "aggs": {
            "groups": {
                "terms": {"field": es_field, "size": top_n, "order": {"_count": "desc"}},
                "aggs": {"sample": {"top_hits": {"size": 1, "_source": ["message", "msg_text"]}}},
            }
        },
    }
    raw = await es.search(index=idx, body=body)
    groups_agg = (raw.get("aggregations") or {}).get("groups", {})
    buckets = groups_agg.get("buckets", [])
    result: dict[str, dict[str, Any]] = {}
    for b in buckets:
        hits = (b.get("sample") or {}).get("hits", {}).get("hits", [])
        sample_msg: str | None = None
        if hits:
            src = hits[0].get("_source", {})
            sample_msg = src.get("message") or src.get("msg_text")
        result[str(b["key"])] = {"count": b["doc_count"], "sample_msg": sample_msg}
    bucket_total = sum(v["count"] for v in result.values())
    total = _hits_total(raw, bucket_total)
    truncated = groups_agg.get("sum_other_doc_count", 0) > 0
    return _Window(result, total, truncated)


async def _run_signature_window(
    es: ElasticsearchClient,
    idx: str,
    system_id: str,
    filter_must: dict[str, Any] | None,
    start: datetime,
    end: datetime,
    profile: LogInvestigationProfile,
    fetch_cap: int,
) -> _Window:
    """Fetch raw docs for one window, group by signature. total is the real window doc count;
    truncated when the window exceeded the fetch cap (counts are then a newest-N sample)."""
    filter_clauses: list[dict[str, Any]] = [
        {"range": {"@timestamp": {"gte": start.isoformat(), "lte": end.isoformat()}}},
        {"term": {"system_id": system_id.upper()}},
        *_build_filter_clauses(filter_must),
    ]
    body: dict[str, Any] = {
        "size": fetch_cap,
        "track_total_hits": True,
        "query": {"bool": {"filter": filter_clauses}},
        "sort": [{"@timestamp": {"order": "desc"}}],
        "_source": ["@timestamp", "message", "msg_text"],
    }
    raw = await es.search(index=idx, body=body)
    hits = (raw.get("hits") or {}).get("hits", [])

    extractor = SignatureExtractor(profile)
    counter: Counter[str] = Counter()
    first_msg: dict[str, str | None] = {}
    for hit in hits:
        src = hit.get("_source", {})
        msg = src.get("message") or src.get("msg_text") or str(src)
        sig = extractor.generate_signature(msg)
        counter[sig] += 1
        if sig not in first_msg:
            first_msg[sig] = msg

    result = {
        sig: {"count": count, "sample_msg": first_msg.get(sig)} for sig, count in counter.items()
    }
    total = _hits_total(raw, len(hits))
    truncated = len(hits) < total or len(hits) >= fetch_cap
    return _Window(result, total, truncated)


class CompareWindowsRequest(BaseModel):
    """Input schema for ``es_compare_windows``."""

    system_id: str = Field(description="Target SAP system id, e.g. 'KHP'.")
    system_type: str | None = None
    window_a: str
    window_b: str
    group_by: Literal["error_code", "component", "host", "log_level", "signature"] = "signature"
    metric: Literal["count"] = "count"
    top_n: int = Field(default=20, ge=1, le=100)
    filter_must: str | None = Field(
        default=None,
        description=(
            "JSON object mapping field names to exact-match values "
            '(e.g. \'{"host":"app01"}\'). Pass null or omit for no pre-filter.'
        ),
    )
    sort: Literal["new_in_b_first", "delta_desc", "count_b_desc", "count_a_desc"] = "new_in_b_first"
    index: str | None = None


async def _run_compare_windows(
    es: ElasticsearchClient,
    *,
    system_id: str,
    window_a: str,
    window_b: str,
    system_type: str | None,
    group_by: str,
    top_n: int,
    filter_must: str | None,
    sort: str,
    index: str | None,
) -> str:
    """Core: parse windows, run both aggregations, diff, and govern the response."""
    coerced = coerce_json_object_arg(filter_must, "filter_must")
    if isinstance(coerced, str):
        return invalid_request(
            "malformed_filter_must",
            details=coerced.removeprefix("__parse_error__:"),
            system_id=system_id,
        )
    filter_must_dict = coerced

    profile = resolve_profile(system_id, system_type)
    if filter_must_dict:
        field_err = validate_field_filters(filter_must_dict, profile)
        if field_err is not None:
            return json.dumps(dict(field_err), default=str)

    try:
        (a_start, a_end), (b_start, b_end) = _parse_windows(window_a, window_b)
    except ValueError as exc:
        return invalid_request(
            str(exc),
            suggestion="Use 'now', 'Nh'/'Nm'/'Nd'/'Nw', 'shift:Xunit', or '<iso>/<iso>'.",
        )

    try:
        query_start = time.perf_counter()
        idx = index or es.default_index

        if group_by != "signature":
            win_a = await _run_terms_agg_window(
                es, idx, system_id, group_by, filter_must_dict, a_start, a_end, top_n
            )
            win_b = await _run_terms_agg_window(
                es, idx, system_id, group_by, filter_must_dict, b_start, b_end, top_n
            )
        else:
            win_a = await _run_signature_window(
                es, idx, system_id, filter_must_dict, a_start, a_end, profile, _SIGNATURE_FETCH_CAP
            )
            win_b = await _run_signature_window(
                es, idx, system_id, filter_must_dict, b_start, b_end, profile, _SIGNATURE_FETCH_CAP
            )

        groups = _diff_bucket_sets(win_a.buckets, win_b.buckets)
        _apply_sort(groups, sort)
        groups = groups[:top_n]

        # total_a/total_b are the true window doc counts (from hits.total), not the sum of the
        # returned buckets — otherwise high-cardinality (>top_n) or >1000-doc windows undercount.
        total_a, total_b = win_a.total, win_b.total
        windows_truncated = win_a.truncated or win_b.truncated
        summary: dict[str, Any] = {
            "total_a": total_a,
            "total_b": total_b,
            "pct_change_total": ((total_b - total_a) / total_a * 100) if total_a > 0 else None,
            "new_groups_count": sum(1 for g in groups if g["is_new_in_b"]),
            "gone_groups_count": sum(1 for g in groups if g["is_gone_in_b"]),
            "window_a_range": f"{a_start.isoformat()} - {a_end.isoformat()}",
            "window_b_range": f"{b_start.isoformat()} - {b_end.isoformat()}",
            # True when a window exceeded top_n distinct keys / the 1000-doc signature sample, so
            # the per-group diff is partial even though total_a/total_b remain exact.
            "partial_diff": windows_truncated,
        }

        query_time_ms = round((time.perf_counter() - query_start) * 1000, 2)

        def _build(groups_slice: list[Any], truncated: bool, reason: str | None) -> str:
            meta = build_response_meta(
                hit_count=len(groups_slice),
                query_time_ms=query_time_ms,
                token_estimate=0,
                truncated=truncated,
                truncated_reason=reason,
            )
            payload = {
                "status": "ok",
                "groups": groups_slice,
                "summary": summary,
                "response_meta": meta,
            }
            meta["token_estimate"] = len(json.dumps(payload, default=str)) // 4
            return json.dumps(payload, default=str)

        result_str, _fitted, _truncated, _reason = fit_items_to_cap(groups, _build, cap=_CAP)
        return result_str

    except Exception:
        _log.exception("es_compare_windows_failed", system_id=system_id)
        return internal_error(
            "Internal error while comparing Elasticsearch windows.", system_id=system_id
        )


@tool("es_compare_windows", args_schema=CompareWindowsRequest, description=_DESCRIPTION)
async def es_compare_windows(
    system_id: str,
    window_a: str,
    window_b: str,
    system_type: str | None = None,
    group_by: str = "signature",
    metric: str = "count",
    top_n: int = 20,
    filter_must: str | None = None,
    sort: str = "new_in_b_first",
    index: str | None = None,
) -> str:
    """Compare two time windows of SAP logs and surface what changed."""
    return await _run_compare_windows(
        get_es_client(),
        system_id=system_id,
        window_a=window_a,
        window_b=window_b,
        system_type=system_type,
        group_by=group_by,
        top_n=top_n,
        filter_must=filter_must,
        sort=sort,
        index=index,
    )
