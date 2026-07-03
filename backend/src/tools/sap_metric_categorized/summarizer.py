"""Pure-python summarization + anomaly detection for the SAP categorized-metrics tool.

Ported from the reference tool's ``_summarize_gauge_metric_results`` / ``_detect_anomalies`` /
``_calculate_trend``, with numpy replaced by pure-python stats (the platform ships no numpy in
v1). Every function here is deterministic and side-effect free, so it is unit-testable without
Prometheus.
"""

from __future__ import annotations

import math
from typing import Any

from src.clients.prometheus import MetricData
from src.logging import get_logger
from src.tools._catalog import MetricCatalog

_log = get_logger(__name__)


def percentile(values_sorted: list[float], p: float) -> float:
    """Linear-interpolation percentile matching numpy's default ``method='linear'``.

    ``values_sorted`` must be sorted ascending and non-empty.
    """
    n = len(values_sorted)
    if n == 1:
        return values_sorted[0]
    rank = (p / 100.0) * (n - 1)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return values_sorted[lo]
    frac = rank - lo
    return values_sorted[lo] + (values_sorted[hi] - values_sorted[lo]) * frac


def calculate_trend(values: list[float]) -> str | None:
    """Direction of a time-ordered series: 'up' / 'down' / 'stable' (None if too short)."""
    n = len(values)
    if n < 10:
        return None
    split = n // 5
    if split == 0:
        return None
    early_avg = sum(values[:split]) / split
    recent_avg = sum(values[-split:]) / split
    if early_avg == 0:
        return None
    ratio = recent_avg / early_avg
    if ratio > 1.10:
        return "up"
    if ratio < 0.90:
        return "down"
    return "stable"


def _aggregate_trend(per_series: list[list[float]]) -> str | None:
    """Trend across series, computed per individual (time-ordered) series then combined.

    A system-wide query (no ``monitoring_context``) returns one series per app server. Trending
    the *concatenation* would compare one server's early points against another's late points —
    meaningless and order-dependent. Instead each series is trended on its own points; a unanimous
    direction is reported, disagreement collapses to ``"mixed"``, and too-short series contribute
    ``None``.
    """
    directions = {calculate_trend(values) for values in per_series}
    directions.discard(None)
    if not directions:
        return None
    if len(directions) == 1:
        return next(iter(directions))
    return "mixed"


def summarize_series(series: list[MetricData]) -> dict[str, Any]:
    """Reduce parsed range series to compact stats (token-efficient for the LLM).

    min/max/avg/percentiles aggregate every finite value across all series (order-independent).
    ``current`` and ``trend`` are computed **per series** and combined deterministically — a
    system-wide query returns one series per app server, and the previous "last series wins"
    behaviour made both fields depend on Prometheus's unspecified series ordering. Percentiles
    are added only with enough points (>= 5). Returns ``{"status": "no_data"}`` when nothing usable
    came back.
    """
    all_values: list[float] = []
    per_series: list[list[float]] = []
    for s in series:
        finite = [v for _, v in s.values if math.isfinite(v)]
        if finite:
            all_values.extend(finite)
            per_series.append(finite)

    if not all_values:
        return {"status": "no_data", "series_count": len(series)}

    # 'current' = the highest current reading across the metric's series. Deterministic (unlike
    # iteration order), and since catalog thresholds are upper bounds this surfaces the hottest
    # app server rather than letting a quiet one mask it.
    current = max(values[-1] for values in per_series)

    summary: dict[str, Any] = {
        "status": "ok",
        "min": round(min(all_values), 2),
        "max": round(max(all_values), 2),
        "avg": round(sum(all_values) / len(all_values), 2),
        "current": round(current, 2),
        "data_points": len(all_values),
        "series_count": len(series),
    }

    if len(all_values) >= 5:
        ordered = sorted(all_values)
        summary["p50"] = round(percentile(ordered, 50), 2)
        summary["p90"] = round(percentile(ordered, 90), 2)
        summary["p95"] = round(percentile(ordered, 95), 2)
        summary["p99"] = round(percentile(ordered, 99), 2)

    trend = _aggregate_trend(per_series)
    if trend:
        summary["trend"] = trend

    return summary


def detect_anomalies(
    summaries: dict[str, dict[str, Any]],
    catalog: MetricCatalog,
) -> list[dict[str, Any]]:
    """Flag metrics whose ``current`` exceeds the catalog ``alert_threshold`` (plus p95 spikes).

    A metric with no numeric threshold (``'-'`` / missing) is not threshold-checked — matching
    the reference, which also skips the spike check in that case.
    """
    anomalies: list[dict[str, Any]] = []

    for key, summary in summaries.items():
        if summary.get("status") != "ok":
            continue

        entry = catalog.entry(key)
        threshold_str = entry.alert_threshold if entry else None
        current = summary.get("current")
        avg = summary.get("avg")
        p95 = summary.get("p95")

        if current is None or avg is None:
            continue
        # No usable threshold ('-' or missing) -> no threshold/spike anomaly (reference parity).
        if threshold_str in (None, "-"):
            continue
        try:
            threshold = float(threshold_str)  # type: ignore[arg-type]
        except (ValueError, TypeError) as exc:
            _log.warning(
                "anomaly_threshold_unparsable",
                metric=key,
                threshold=threshold_str,
                error=str(exc),
            )
            continue

        if current > threshold:
            delta_pct = round(((current - avg) / avg * 100), 1) if avg and avg > 0 else 0.0
            severity = "critical" if current > threshold * 1.5 else "warning"
            description = entry.description if entry and entry.description else key
            anomalies.append(
                {
                    "metric": key,
                    "severity": severity,
                    "current_value": current,
                    "threshold": threshold,
                    "delta": f"+{delta_pct}%" if delta_pct > 0 else f"{delta_pct}%",
                    "description": f"{description} exceeds threshold",
                }
            )

        if p95 and avg and avg > 0 and p95 > avg * 1.5:
            anomalies.append(
                {
                    "metric": key,
                    "severity": "info",
                    "pattern": "spike_detected",
                    "description": f"{key} shows significant spikes (p95: {p95}, avg: {avg})",
                }
            )

    return anomalies
