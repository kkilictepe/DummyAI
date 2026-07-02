"""Elasticsearch log tools for the copilot agent.

Six governed primitives over the single shared Elasticsearch client. Each is one async ``@tool``
returning a governance-capped JSON string; the reference's per-request registry proxy pattern is
collapsed away (Dummy AI has exactly one Elasticsearch).
"""

from __future__ import annotations

from src.tools.elasticsearch.tool_es_aggregation import es_aggregation
from src.tools.elasticsearch.tool_es_cluster_errors import es_cluster_errors
from src.tools.elasticsearch.tool_es_compare_windows import es_compare_windows
from src.tools.elasticsearch.tool_es_drilldown_around import es_drilldown_around
from src.tools.elasticsearch.tool_es_field_search import es_field_search
from src.tools.elasticsearch.tool_es_raw_query import es_raw_query

__all__ = [
    "es_aggregation",
    "es_cluster_errors",
    "es_compare_windows",
    "es_drilldown_around",
    "es_field_search",
    "es_raw_query",
]
