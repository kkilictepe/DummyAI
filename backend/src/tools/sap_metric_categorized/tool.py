"""``tool_sap_metric_categorized`` — categorized SAP metrics from Prometheus, summarized.

Ported from the reference ``ToolSapMetricCategorizedRootis``, collapsing the ``BaseTool`` +
``PrometheusQueryTool`` wrapper into one async ``@tool`` that talks to the single shared
:class:`~src.clients.prometheus.PrometheusClient`. numpy is gone (pure-python stats live in
:mod:`.summarizer`); per-system routing is gone (``system_id`` is a PromQL label value).

Flow: category -> profile's logical keys -> catalog Prometheus names ->
``name{system_id="X"[, monitoring_context="Y"]}`` -> concurrent range queries -> per-metric
summary -> threshold anomalies. When the caller did not pin a ``monitoring_context``, the
available application servers are surfaced (derived from the ``monitoring_context`` labels of the
series actually returned — so the drill-down list is scoped to *this* system by construction,
not leaked from others).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from src.clients import get_prometheus_client
from src.clients.prometheus import MetricData
from src.tools._catalog import MetricCatalog
from src.tools._time import resolve_range
from src.tools.sap_metric_categorized.context_validation import validate_monitoring_context
from src.tools.sap_metric_categorized.summarizer import detect_anomalies, summarize_series

Category = Literal[
    "sap_resource_usage",
    "hana_db_resource_usage",
    "infrastructure_resource_usage",
    "error_and_warnings",
    "integration_and_data_transfer",
    # Composite (cross-layer) categories.
    "cpu_overview",
    "memory_overview",
    "system_overview",
    "workprocess_overview",
]


class SapMetricCategorizedInput(BaseModel):
    """Arguments for ``tool_sap_metric_categorized``."""

    system_id: str = Field(description="Target SAP system id, e.g. 'KHP', 'KBP', 'PROD01'.")
    category: Category = Field(description="Metric category / profile to query.")
    time_range: str = Field(
        default="5m",
        description="Relative window ending at 'end': '5m', '30m', '3h', '24h', '7d'. "
        "Use '5m' if the user did not specify.",
    )
    monitoring_context: str | None = Field(
        default=None,
        description="Optional app-server / instance id to narrow to one SAP instance. "
        "Omit for a system-wide view (and for hana_db_resource_usage, which has no instance).",
    )
    end: str | None = Field(
        default=None,
        description="Optional window end (ISO-8601 or Unix seconds). Defaults to now.",
    )
    step: str | None = Field(
        default=None,
        description="Optional query resolution, e.g. '1m', '5m', '1h'. Defaults to '5m'.",
    )


def _escape_label_value(value: str) -> str:
    """Escape a PromQL double-quoted label value (backslash then quote)."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _build_promql(prometheus_name: str, system_id: str, monitoring_context: str | None) -> str:
    """``name{system_id="X"[, monitoring_context="Y"]}`` with escaped label values."""
    selectors = [f'system_id="{_escape_label_value(system_id)}"']
    if monitoring_context:
        selectors.append(f'monitoring_context="{_escape_label_value(monitoring_context)}"')
    return f"{prometheus_name}{{{', '.join(selectors)}}}"


def _error(system_id: str, category: str, message: str) -> dict[str, Any]:
    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "system_id": system_id,
        "category": category,
        "status": "error",
        "error": message,
    }


@tool("tool_sap_metric_categorized", args_schema=SapMetricCategorizedInput)
async def tool_sap_metric_categorized(
    system_id: str,
    category: Category,
    time_range: str = "5m",
    monitoring_context: str | None = None,
    end: str | None = None,
    step: str | None = None,
) -> dict[str, Any]:
    """Retrieve SAP/HANA metrics for a category and return summarized stats + anomalies.

    Returns per-metric summaries (min/max/avg/current/percentiles/trend), anomalies vs the
    catalog alert thresholds, and — when no ``monitoring_context`` was pinned —
    ``available_application_servers`` for optional per-server drill-down.

    If a pinned ``monitoring_context`` does not exist for the system, returns
    ``status="invalid_label_filter"`` with the ``available`` values and a ``suggestion`` and runs
    no range queries: present the available values to the operator and ask which to use.
    """
    if not system_id:
        return _error(system_id, category, "system_id is required")

    catalog = MetricCatalog.load()
    if not catalog.has_profile(category):
        available = ", ".join(catalog.profiles())
        return _error(system_id, category, f"Unknown category. Available: {available}")

    metric_keys = catalog.profile_keys(category)
    if not metric_keys:
        return _error(system_id, category, f"No metrics defined for category '{category}'")

    step = step or "5m"
    try:
        start_ts, end_ts = resolve_range(time_range, end)
    except ValueError as exc:
        return _error(system_id, category, str(exc))

    # Build (logical_key -> PromQL) for every key that maps to a Prometheus name.
    queries: list[tuple[str, str]] = []
    for key in metric_keys:
        prometheus_name = catalog.prometheus_name(key)
        if prometheus_name:
            queries.append((key, _build_promql(prometheus_name, system_id, monitoring_context)))

    client = get_prometheus_client()

    # When the caller pinned a monitoring_context, validate it against the live label values so a
    # typo returns a helpful suggestion (invalid_label_filter) instead of a silent no_data. Runs
    # after the cheap local checks and before any range query. Fail-open: an unverifiable context
    # (empty discovery) proceeds untouched.
    if monitoring_context:
        invalid = await validate_monitoring_context(client, system_id, monitoring_context)
        if invalid is not None:
            return {
                "timestamp": datetime.now(UTC).isoformat(),
                "system_id": system_id,
                "category": category,
                "status": "invalid_label_filter",
                **invalid,
            }

    responses = await client.query_multiple(queries, start_ts, end_ts, step)

    summaries: dict[str, dict[str, Any]] = {}
    app_servers: set[str] = set()
    for key in metric_keys:
        response = responses.get(key)
        if response is None:
            summaries[key] = {"status": "error", "error": "query dropped"}
            continue
        if not response.success:
            summaries[key] = {"status": "error", "error": response.error_message or "query failed"}
            continue
        series: list[MetricData] = client.parse_metric_data(response)
        summaries[key] = summarize_series(series)
        for s in series:
            ctx = s.labels.get("monitoring_context")
            if ctx:
                app_servers.add(ctx)

    anomalies = detect_anomalies(summaries, catalog)

    metrics_ok = [k for k, v in summaries.items() if v.get("status") == "ok"]
    metrics_no_data = [k for k, v in summaries.items() if v.get("status") == "no_data"]
    metrics_failed = [k for k, v in summaries.items() if v.get("status") == "error"]

    response_body: dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "system_id": system_id,
        "monitoring_context": monitoring_context,
        "category": category,
        "time_range": {"start": start_ts, "end": end_ts, "step": step},
        "summaries": summaries,
        "anomalies": anomalies,
        "context": {
            "metric_definitions": _metric_definitions(metric_keys, catalog),
            "query_metadata": {
                "metrics_queried": len(metric_keys),
                "metrics_successful": len(metrics_ok),
                "metrics_no_data": len(metrics_no_data),
                "metrics_failed": len(metrics_failed),
                "category_type": category,
            },
            "metrics_successful": metrics_ok,
            "metrics_with_issues": {
                "no_data": [
                    {
                        "metric": k,
                        "description": _description(k, catalog),
                        "reason": "No data returned from Prometheus for this metric",
                    }
                    for k in metrics_no_data
                ],
                "failed": [
                    {
                        "metric": k,
                        "description": _description(k, catalog),
                        "error": summaries[k].get("error", "Unknown error"),
                    }
                    for k in metrics_failed
                ],
            },
        },
        "status": "success",
    }

    # Offer per-server drill-down only when the caller did not already pin one.
    if not monitoring_context and metrics_ok and app_servers:
        response_body["available_application_servers"] = sorted(app_servers)

    return response_body


def _metric_definitions(metric_keys: list[str], catalog: MetricCatalog) -> list[dict[str, Any]]:
    definitions: list[dict[str, Any]] = []
    for key in metric_keys:
        entry = catalog.entry(key)
        if entry is None:
            continue
        definition: dict[str, Any] = {
            "metric": key,
            "description": entry.description or "No description available",
        }
        if entry.unit:
            definition["unit"] = entry.unit
        definitions.append(definition)
    return definitions


def _description(key: str, catalog: MetricCatalog) -> str:
    entry = catalog.entry(key)
    return entry.description if entry and entry.description else key
