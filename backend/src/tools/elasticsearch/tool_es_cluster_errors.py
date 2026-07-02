"""``es_cluster_errors`` — group similar log messages into signature-based clusters.

One async ``@tool`` that fetches logs, normalizes variable tokens (ids, paths, hostnames) via the
profile-aware ``LogNormalizer``, then groups messages sharing a template into clusters ranked by
occurrence count.
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
from src.tools.elasticsearch.shared.clustering import ClusteringConfig, LogClusterEngine
from src.tools.elasticsearch.shared.normalizer import LogNormalizer
from src.tools.elasticsearch.shared.response_governance import (
    build_response_meta,
    coerce_json_object_arg,
    fit_items_to_cap,
    validate_field_filters,
)
from src.tools.elasticsearch.shared.time_range import parse_time_range

_log = get_logger(__name__)

_CAP = 256_000

_DESCRIPTION = (
    "Fetch logs from Elasticsearch and group them into signature-based clusters. Normalizes "
    "variable tokens (ids, paths, hostnames) via regex, then groups messages sharing a template "
    "into clusters ranked by occurrence count. Accepts the same query parameters as "
    "es_field_search: system_id, time_range, must_match (JSON field->value pairs), text_search, "
    "log_level, exclude_patterns. Controls: top_n (max clusters, default 20), min_cluster_size "
    "(min occurrences, default 2), max_fetch (docs fetched before clustering, default 1000, max "
    "5000), group_by_system (default true), group_by_component (default false). Returns "
    "{status:'ok', clusters:[{signature_pattern, count, sample_message, sample_doc_id, first_seen, "
    "last_seen, avg_interval_seconds, unique_hosts}], total_docs_examined, response_meta}. Unknown "
    "must_match field names return {status:'invalid_request'} without querying ES."
)


class ClusterErrorsRequest(BaseModel):
    """Input schema for ``es_cluster_errors``."""

    system_id: str = Field(description="Target SAP system id, e.g. 'KHP'.")
    system_type: str | None = None
    time_range: str = Field(
        ...,
        description=(
            "Time window: a relative duration — '3h', '24h', '7d', '30m', '1w' — or an absolute "
            "ISO-8601 pair '<iso>/<iso>'."
        ),
    )
    must_match: str | None = Field(
        default=None,
        description=(
            "JSON object mapping field names to exact-match values "
            '(e.g. \'{"host.name":"app01"}\'). Pass null or omit to match all documents.'
        ),
    )
    text_search: str | None = None
    log_level: str | None = None
    exclude_patterns: list[str] = Field(default_factory=list)
    top_n: int = Field(default=20, ge=1, le=100)
    min_cluster_size: int = Field(default=2, ge=1, le=50)
    max_fetch: int = Field(default=1000, ge=100, le=5000)
    group_by_system: bool = True
    group_by_component: bool = False


def _no_clusters_payload(query_time_ms: float) -> str:
    meta = build_response_meta(
        hit_count=0, query_time_ms=query_time_ms, token_estimate=0, truncated=False
    )
    payload = {"status": "ok", "clusters": [], "total_docs_examined": 0, "response_meta": meta}
    meta["token_estimate"] = len(json.dumps(payload, default=str)) // 4
    return json.dumps(payload, default=str)


async def _run_cluster_errors(
    es: ElasticsearchClient,
    *,
    system_id: str,
    time_range: str,
    system_type: str | None,
    must_match: str | None,
    text_search: str | None,
    log_level: str | None,
    exclude_patterns: list[str],
    top_n: int,
    min_cluster_size: int,
    max_fetch: int,
    group_by_system: bool,
    group_by_component: bool,
) -> str:
    """Core: fetch, normalize, cluster, and govern the response."""
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
    if must_match_dict:
        field_err = validate_field_filters(must_match_dict, profile)
        if field_err is not None:
            return json.dumps(dict(field_err), default=str)

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

        # Omit the _source restriction — the normalizer needs the full document.
        body: dict[str, Any] = {
            "query": {
                "bool": {
                    "filter": filter_clauses,
                    "must_not": must_not_clauses,
                    "must": must_clauses,
                }
            },
            "size": max_fetch,
            "sort": [{"@timestamp": "asc"}],
            "track_total_hits": True,
        }

        index_name = es.default_index
        raw = await es.search(index=index_name, body=body)
        query_time_ms = round((time.perf_counter() - query_start) * 1000, 2)

        raw_hits = (raw.get("hits") or {}).get("hits") or []
        total_block = (raw.get("hits") or {}).get("total", {})
        if isinstance(total_block, dict):
            total_docs_examined = total_block.get("value", len(raw_hits))
        else:
            total_docs_examined = int(total_block) if total_block else len(raw_hits)

        if not raw_hits:
            return _no_clusters_payload(query_time_ms)

        sources = [h.get("_source", {}) for h in raw_hits]
        doc_ids = [h.get("_id", "") for h in raw_hits]
        normalizer = LogNormalizer(profile=profile)
        normalized = normalizer.normalize_batch(
            raw_logs=sources, doc_ids=doc_ids, index_name=index_name
        )

        clustering_cfg = ClusteringConfig(
            min_cluster_size=min_cluster_size,
            max_samples_per_cluster=1,  # one sample is enough for compact output
        )
        engine = LogClusterEngine(config=clustering_cfg, normalizer=normalizer)
        clusters = engine.cluster_logs(
            normalized,
            group_by_system=group_by_system,
            group_by_component=group_by_component,
        )

        compact: list[dict[str, Any]] = []
        for c in clusters[:top_n]:
            first_sample = c.sample_logs[0] if c.sample_logs else None
            compact.append(
                {
                    "signature_pattern": c.signature_pattern,
                    "count": c.occurrence_count,
                    "sample_message": first_sample.raw_message if first_sample else "",
                    "sample_doc_id": first_sample.doc_id if first_sample else "",
                    "first_seen": c.first_seen.isoformat(),
                    "last_seen": c.last_seen.isoformat(),
                    "avg_interval_seconds": c.avg_interval_seconds,
                    "unique_hosts": c.unique_hosts,
                }
            )

        def _build(items_slice: list[Any], truncated: bool, reason: str | None) -> str:
            meta = build_response_meta(
                hit_count=len(items_slice),
                query_time_ms=query_time_ms,
                token_estimate=0,
                truncated=truncated,
                truncated_reason=reason,
            )
            payload = {
                "status": "ok",
                "clusters": items_slice,
                "total_docs_examined": total_docs_examined,
                "response_meta": meta,
            }
            meta["token_estimate"] = len(json.dumps(payload, default=str)) // 4
            return json.dumps(payload, default=str)

        result_str, _fitted, _truncated, _reason = fit_items_to_cap(compact, _build, cap=_CAP)
        return result_str

    except Exception:
        _log.exception("es_cluster_errors_failed", system_id=system_id)
        return internal_error(
            "Internal error while clustering Elasticsearch logs.", system_id=system_id
        )


@tool("es_cluster_errors", args_schema=ClusterErrorsRequest, description=_DESCRIPTION)
async def es_cluster_errors(
    system_id: str,
    time_range: str,
    system_type: str | None = None,
    must_match: str | None = None,
    text_search: str | None = None,
    log_level: str | None = None,
    exclude_patterns: list[str] | None = None,
    top_n: int = 20,
    min_cluster_size: int = 2,
    max_fetch: int = 1000,
    group_by_system: bool = True,
    group_by_component: bool = False,
) -> str:
    """Cluster SAP logs by message signature and rank by occurrence count."""
    return await _run_cluster_errors(
        get_es_client(),
        system_id=system_id,
        time_range=time_range,
        system_type=system_type,
        must_match=must_match,
        text_search=text_search,
        log_level=log_level,
        exclude_patterns=exclude_patterns or [],
        top_n=top_n,
        min_cluster_size=min_cluster_size,
        max_fetch=max_fetch,
        group_by_system=group_by_system,
        group_by_component=group_by_component,
    )
