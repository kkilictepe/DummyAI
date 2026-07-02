"""LangChain tools for the agent flows + the curated ``get_all_tools()`` export.

Agents import :func:`get_all_tools`, never individual tool modules, so the tool roster is
defined in exactly one place. Metric tools land first (Phase 2); the Prometheus advanced-query
tool (Phase 4) and the Elasticsearch tools (Phase 5) append here as they arrive.

Firewall note: every tool below reads only the non-secret YAML loaders / the shared clients —
none imports ``get_settings()``, so a tool can never touch a credential.
"""

from __future__ import annotations

from langchain_core.tools import BaseTool

from src.tools.elasticsearch import (
    es_aggregation,
    es_cluster_errors,
    es_compare_windows,
    es_drilldown_around,
    es_field_search,
    es_raw_query,
)
from src.tools.metric_lookup import metric_lookup
from src.tools.prometheus_advanced_query import prometheus_metrics_advance_query
from src.tools.sap_metric_categorized import tool_sap_metric_categorized
from src.tools.systems import list_sap_systems


def get_all_tools() -> list[BaseTool]:
    """Return the curated tool list bound to the Copilot agent."""
    return [
        tool_sap_metric_categorized,
        metric_lookup,
        prometheus_metrics_advance_query,
        list_sap_systems,
        es_field_search,
        es_aggregation,
        es_compare_windows,
        es_drilldown_around,
        es_cluster_errors,
        es_raw_query,
    ]


__all__ = [
    "es_aggregation",
    "es_cluster_errors",
    "es_compare_windows",
    "es_drilldown_around",
    "es_field_search",
    "es_raw_query",
    "get_all_tools",
    "list_sap_systems",
    "metric_lookup",
    "prometheus_metrics_advance_query",
    "tool_sap_metric_categorized",
]
