"""``prometheus_metrics_advance_query`` — advanced PromQL analysis for the copilot agent.

Thin async ``@tool`` over :class:`~src.tools.prometheus_advanced_query.engine.QueryEngine`. The
LLM supplies pre-approved metric names (from ``metric_lookup``), a time range, and a query type;
the engine builds safe selectors, runs them against the single shared Prometheus client, and
returns structured results with anomalies / baseline / correlation analysis.

``system_id`` is folded into the ``system_id`` PromQL label here (it is a label value, never a
client-routing key), taking precedence over any ``system_id`` the LLM put in ``labels``.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from src.clients import get_prometheus_client
from src.logging import get_logger
from src.tools.prometheus_advanced_query.engine import build_query_engine
from src.tools.prometheus_advanced_query.schemas import (
    CorrelationInput,
    PrometheusAdvanceQueryInput,
    QueryType,
    TimeRangeInput,
)

_log = get_logger(__name__)

_DESCRIPTION = """Query Prometheus metrics with advanced analysis for SAP system monitoring.

Call `metric_lookup` FIRST to obtain valid metric names, then pass them here.

REQUIRED:
- metric_names: 1-10 pre-approved Prometheus metric names.
- time_range: {start, end} in ISO-8601 plus step (e.g. '60s', '5m').

OPTIONAL:
- query_type: 'instant' | 'range' (default) | 'anomaly_check' | 'baseline_compare' | 'correlation'.
- system_id: SAP system id to scope to (e.g. 'KHP'); added as the system_id label.
- labels: extra label filters, e.g. {"monitoring_context": "app01"}.
- correlation: REQUIRED only when query_type='correlation'; needs method ('pearson' or
  'time_shifted') and reference_metric (must be one of metric_names).

QUERY TYPES:
- instant: current value at a single point in time.
- range: time series over the window (default).
- anomaly_check: range query + Z-score / moving-average / spike anomaly detection.
- baseline_compare: compare the current window to the same window 24h earlier.
- correlation: correlate metrics against a reference metric.

Returns structured results: per-metric summary statistics, trends, anomalies, and RCA insights."""


@tool(
    "prometheus_metrics_advance_query",
    args_schema=PrometheusAdvanceQueryInput,
    description=_DESCRIPTION,
)
async def prometheus_metrics_advance_query(
    metric_names: list[str],
    time_range: TimeRangeInput,
    query_type: QueryType = QueryType.RANGE,
    labels: dict[str, str] | None = None,
    correlation: CorrelationInput | None = None,
    system_id: str | None = None,
) -> dict[str, Any]:
    """Execute an advanced Prometheus query and return structured analysis (see the description)."""
    _log.debug(
        "advanced_query_tool_called",
        query_type=query_type.value,
        metric_count=len(metric_names),
        system_id=system_id,
    )
    folded_labels = dict(labels or {})
    if system_id:
        folded_labels["system_id"] = system_id

    input_data = PrometheusAdvanceQueryInput(
        metric_names=metric_names,
        time_range=time_range,
        query_type=query_type,
        labels=folded_labels or None,
        correlation=correlation,
        system_id=system_id,
    )

    engine = build_query_engine()
    client = get_prometheus_client()
    result = await engine.run(input_data, client)
    if result.status == "error":
        _log.warning(
            "advanced_query_tool_error",
            query_type=query_type.value,
            system_id=system_id,
            error=result.error,
        )
    return result.model_dump(mode="json")
