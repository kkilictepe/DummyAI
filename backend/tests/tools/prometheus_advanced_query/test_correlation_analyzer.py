"""CorrelationAnalyzer: pure-python Pearson, strength, ranking, insufficient-data handling."""

from __future__ import annotations

import pytest

from src.tools.prometheus_advanced_query.correlation_analyzer import CorrelationAnalyzer
from src.tools.prometheus_advanced_query.schemas import CorrelationMethod, CorrelationStrength

from ._helpers import make_series_result

_ANALYZER = CorrelationAnalyzer()

_RISING = [float(i) for i in range(12)]
_FALLING = [float(11 - i) for i in range(12)]
# A distinctive, non-monotonic pattern so correlation is maximized ONLY at the true lag.
_PATTERN = [1.0, 3.0, 7.0, 2.0, 9.0, 4.0, 8.0, 5.0, 6.0, 10.0, 2.0, 7.0]


def test_pearson_identical_series_is_one() -> None:
    assert _ANALYZER._pearson_pure_python(_RISING, _RISING) == pytest.approx(1.0)


def test_pearson_anticorrelated_is_minus_one() -> None:
    assert _ANALYZER._pearson_pure_python(_RISING, _FALLING) == pytest.approx(-1.0)


def test_pearson_constant_series_is_zero() -> None:
    # Zero variance -> undefined correlation, defined here as 0.0 (no linear relationship).
    assert _ANALYZER._pearson_pure_python([5.0] * 12, _RISING) == 0.0


def test_no_p_value_without_scipy() -> None:
    ref = make_series_result("a", _RISING)
    other = make_series_result("b", _RISING)
    result = _ANALYZER.compute_correlation(ref, other)
    assert result.coefficient == 1.0
    assert result.strength == CorrelationStrength.STRONG
    assert result.p_value is None  # pure-python path never computes a p-value
    assert result.method == CorrelationMethod.PEARSON


def test_insufficient_aligned_points_returns_weak_zero() -> None:
    # Only 4 aligned points, below the min_data_points floor (10).
    ref = make_series_result("a", [1.0, 2.0, 3.0, 4.0])
    other = make_series_result("b", [1.0, 2.0, 3.0, 4.0])
    result = _ANALYZER.compute_correlation(ref, other)
    assert result.coefficient == 0.0
    assert result.strength == CorrelationStrength.WEAK
    assert "Insufficient data" in result.interpretation


def test_compute_all_sorted_by_absolute_coefficient() -> None:
    reference = make_series_result("ref", _RISING)
    strong_neg = make_series_result("strong_neg", _FALLING)  # r = -1.0
    weak = make_series_result("weak", [1.0, 5.0, 2.0, 8.0, 3.0, 1.0, 9.0, 2.0, 4.0, 1.0, 7.0, 3.0])
    results = _ANALYZER.compute_all_correlations([reference, strong_neg, weak], "ref")
    # Reference is excluded; strongest |r| first.
    assert [r.metric_b for r in results] == ["strong_neg", "weak"]
    assert abs(results[0].coefficient) >= abs(results[1].coefficient)


def test_missing_reference_metric_returns_empty() -> None:
    results = _ANALYZER.compute_all_correlations([make_series_result("a", _RISING)], "nope")
    assert results == []


def test_strength_classification_boundaries() -> None:
    assert _ANALYZER._classify_strength(0.7) == CorrelationStrength.STRONG
    assert _ANALYZER._classify_strength(0.5) == CorrelationStrength.MODERATE
    assert _ANALYZER._classify_strength(0.1) == CorrelationStrength.WEAK


def test_time_shifted_recovers_known_lead_lag() -> None:
    # b is a's pattern delayed by 120s (b's samples happen 2 steps later), so a leads b by 120s.
    ref = make_series_result("a", _PATTERN, start_ts=0, step=60)
    delayed = make_series_result("b", _PATTERN, start_ts=120, step=60)
    result = _ANALYZER.compute_correlation(
        ref, delayed, method=CorrelationMethod.TIME_SHIFTED, max_lag_seconds=300
    )
    assert result.method == CorrelationMethod.TIME_SHIFTED
    assert result.coefficient == 1.0
    assert result.lag_seconds == 120
    assert "a leads b by 120 seconds" in result.interpretation


def test_time_shifted_identical_series_at_15s_step_reports_zero_lag() -> None:
    # Regression for the fixed alignment bug: at a 15s step (smaller than the old 30s tolerance),
    # two IDENTICAL series must yield lag 0, not a spurious lead/lag from many-to-one collapse.
    ref = make_series_result("a", _PATTERN, start_ts=0, step=15)
    same = make_series_result("b", _PATTERN, start_ts=0, step=15)
    result = _ANALYZER.compute_correlation(
        ref, same, method=CorrelationMethod.TIME_SHIFTED, max_lag_seconds=300
    )
    assert result.coefficient == 1.0
    assert result.lag_seconds == 0
