"""AnomalyDetector: Z-score / spike detection, severity, summary reporting."""

from __future__ import annotations

from src.tools.prometheus_advanced_query.anomaly_detector import (
    AnomalyDetectionConfig,
    AnomalyDetector,
)

from ._helpers import make_series_result

_DETECTOR = AnomalyDetector()


def test_single_large_outlier_is_detected() -> None:
    # 14 flat points + one 10x spike: Z-score exceeds 3 (max |Z| = sqrt(n-1) here) and the spike
    # detector also fires. The outlier is a single index, so anomaly_count is 1.
    result = make_series_result("m", [10.0] * 14 + [100.0])
    out = _DETECTOR.detect_anomalies(result)
    assert out.anomalies is not None
    assert out.anomalies.detected is True
    assert out.anomalies.z_score_anomalies >= 1
    assert out.anomalies.max_z_score is not None and out.anomalies.max_z_score > 3.0


def test_flat_series_has_no_anomalies() -> None:
    out = _DETECTOR.detect_anomalies(make_series_result("m", [42.0] * 15))
    assert out.anomalies is not None
    assert out.anomalies.detected is False
    assert out.anomalies.severity == "none"


def test_too_few_points_short_circuits() -> None:
    # Below min_data_points (5): detection is skipped, not errored.
    out = _DETECTOR.detect_anomalies(make_series_result("m", [1.0, 999.0, 2.0]))
    assert out.anomalies is not None
    assert out.anomalies.detected is False


def test_static_threshold_rule_flags_breaches() -> None:
    # Explicit max rule: the two points above 50 are flagged as threshold breaches.
    result = make_series_result("m", [10.0, 20.0, 30.0, 60.0, 70.0, 15.0])
    out = _DETECTOR.detect_anomalies(result, threshold_rules={"max": 50.0})
    assert out.anomalies is not None
    assert out.anomalies.threshold_breaches == 2


def test_flat_window_tiny_drift_is_not_an_anomaly() -> None:
    # Steady-state SAP metric with a 0.001% wobble off a perfectly flat window: the zero-variance
    # moving-average branch must NOT flag it (epsilon guard), and z-score/spike stay silent too.
    out = _DETECTOR.detect_anomalies(make_series_result("m", [10.0] * 5 + [10.0001]))
    assert out.anomalies is not None
    assert out.anomalies.detected is False


def test_moving_avg_flags_real_jump_off_flat_window() -> None:
    # But a materially large jump off the same flat window IS flagged by the moving-avg detector.
    assert _DETECTOR._detect_moving_avg_anomalies([10.0] * 5 + [50.0]) == [5]


def test_spike_detector_catches_downward_collapse() -> None:
    # A crash from a high baseline to near-zero (150 -> 1) is a spike in the DOWN direction; the
    # symmetric ratio catches it where the old curr/prev ratio (0.007) silently missed it.
    assert _DETECTOR._detect_spike_anomalies([100.0, 100.0, 100.0, 10.0]) == [3]
    assert _DETECTOR._detect_spike_anomalies([50.0, 0.0]) == [1]  # drop straight to zero


def test_oscillating_baseline_then_crash_is_detected_end_to_end() -> None:
    # Regression for the confirmed false negative: an oscillating throughput baseline whose final
    # sample collapses (150 -> 1). The oscillation inflates std so z-score/moving-avg miss it;
    # only the symmetric spike ratio catches the crash.
    values = [50.0, 150.0] * 7 + [1.0]
    out = _DETECTOR.detect_anomalies(make_series_result("m", values))
    assert out.anomalies is not None
    assert out.anomalies.detected is True


def test_severity_tiers() -> None:
    assert _DETECTOR._classify_severity(0) == "none"
    assert _DETECTOR._classify_severity(1) == "low"
    assert _DETECTOR._classify_severity(3) == "medium"
    assert _DETECTOR._classify_severity(5) == "high"
    assert _DETECTOR._classify_severity(10) == "critical"


def test_z_score_threshold_is_configurable() -> None:
    # A very high threshold suppresses the Z-score detector (spike/moving-avg may still fire).
    lax = AnomalyDetector(AnomalyDetectionConfig(z_score_threshold=100.0))
    out = lax.detect_anomalies(make_series_result("m", [10.0] * 14 + [100.0]))
    assert out.anomalies is not None
    assert out.anomalies.z_score_anomalies == 0


def test_summary_ranks_by_severity() -> None:
    calm = _DETECTOR.detect_anomalies(make_series_result("calm", [10.0] * 15))
    spiky = _DETECTOR.detect_anomalies(make_series_result("spiky", [10.0] * 14 + [500.0]))
    summary = _DETECTOR.get_anomaly_summary([calm, spiky])
    assert summary["metrics_affected"] == 1
    assert summary["affected_metrics"][0]["metric"] == "spiky"
    report = _DETECTOR.format_anomaly_report([calm, spiky])
    assert "Anomaly Detection Report" in report
    assert "spiky" in report
