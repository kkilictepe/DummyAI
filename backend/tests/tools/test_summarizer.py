"""Pure-python summarizer: percentiles, trend, no_data, multi-series determinism, anomalies."""

from __future__ import annotations

import math

from src.clients.prometheus import MetricData
from src.tools._catalog import MetricCatalog
from src.tools.sap_metric_categorized.summarizer import (
    calculate_trend,
    detect_anomalies,
    percentile,
    summarize_series,
)


def _series(values: list[float], monitoring_context: str | None = None) -> MetricData:
    labels = {"monitoring_context": monitoring_context} if monitoring_context else {}
    return MetricData("m", labels, [(float(i), v) for i, v in enumerate(values)])


# -- percentile -------------------------------------------------------------


def test_percentile_linear_interpolation() -> None:
    ordered = [70.0, 72.0, 80.0, 85.0, 88.0, 90.0]
    assert percentile(ordered, 50) == 82.5
    assert percentile(ordered, 90) == 89.0
    assert percentile(ordered, 95) == 89.5


def test_percentile_single_value() -> None:
    assert percentile([42.0], 99) == 42.0


# -- calculate_trend --------------------------------------------------------


def test_trend_up_down_stable() -> None:
    assert calculate_trend([10, 10, 10, 10, 10, 10, 10, 10, 20, 20]) == "up"
    assert calculate_trend([20, 20, 10, 10, 10, 10, 10, 10, 10, 10]) == "down"
    assert calculate_trend([10] * 10) == "stable"


def test_trend_none_when_too_few_points() -> None:
    assert calculate_trend([1, 2, 3, 4, 5, 6, 7, 8, 9]) is None  # < 10


def test_trend_none_when_early_avg_zero() -> None:
    # early window is all-zero -> ratio undefined -> None (no divide-by-zero)
    assert calculate_trend([0, 0, 5, 5, 5, 5, 5, 5, 5, 5]) is None


# -- summarize_series -------------------------------------------------------


def test_no_data_on_empty_series_list() -> None:
    assert summarize_series([]) == {"status": "no_data", "series_count": 0}


def test_no_data_when_all_values_nan() -> None:
    result = summarize_series([MetricData("m", {}, [(1.0, math.nan), (2.0, math.nan)])])
    assert result == {"status": "no_data", "series_count": 1}


def test_current_is_deterministic_max_across_app_server_series() -> None:
    hot = _series([80.0] * 12, "APP01")
    cold = _series([20.0] * 12, "APP02")
    # 'current' is the max of per-series last values, regardless of series order
    assert summarize_series([hot, cold])["current"] == 80.0
    assert summarize_series([cold, hot])["current"] == 80.0


def test_trend_is_per_series_not_cross_series_concatenation() -> None:
    # Two flat-but-different-level servers: neither series trends, so the aggregate must be
    # 'stable' — not the spurious 'up'/'down' the old concatenation produced, and order-stable.
    hot = _series([80.0] * 12, "APP01")
    cold = _series([20.0] * 12, "APP02")
    assert summarize_series([hot, cold])["trend"] == "stable"
    assert summarize_series([cold, hot])["trend"] == "stable"


def test_trend_mixed_when_series_disagree() -> None:
    rising = _series([10, 10, 10, 10, 10, 10, 10, 10, 20, 20], "APP01")
    falling = _series([20, 20, 10, 10, 10, 10, 10, 10, 10, 10], "APP02")
    assert summarize_series([rising, falling])["trend"] == "mixed"


def test_pooled_stats_are_order_independent() -> None:
    a = _series([10.0, 30.0], "APP01")
    b = _series([20.0, 40.0], "APP02")
    forward = summarize_series([a, b])
    backward = summarize_series([b, a])
    for key in ("min", "max", "avg", "data_points"):
        assert forward[key] == backward[key]
    assert forward["min"] == 10.0
    assert forward["max"] == 40.0


# -- detect_anomalies -------------------------------------------------------


def test_spike_detected_branch() -> None:
    # current (50) is below the catalog threshold (75) so no threshold anomaly, but p95 (30) is
    # more than 1.5x the avg (10) -> an info 'spike_detected' anomaly.
    summaries = {
        "sap_application_cpu_utilisation": {
            "status": "ok",
            "current": 50.0,
            "avg": 10.0,
            "p95": 30.0,
        }
    }
    anomalies = detect_anomalies(summaries, MetricCatalog.load())
    assert len(anomalies) == 1
    assert anomalies[0]["pattern"] == "spike_detected"
    assert anomalies[0]["severity"] == "info"


def test_no_threshold_metric_is_not_flagged() -> None:
    # bgrfc has alert_threshold '-' (not applicable) -> never flagged, even far above any value.
    summaries = {
        "sap_application_bgrfc_inbound_throughput_actual": {
            "status": "ok",
            "current": 9999.0,
            "avg": 1.0,
            "p95": 9999.0,
        }
    }
    assert detect_anomalies(summaries, MetricCatalog.load()) == []
