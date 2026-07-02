"""Deterministic anomaly detection for time-series data.

Ported from the reference ``anomaly_detector.py``. Combines Z-score, moving-average deviation,
optional static-threshold, and spike detection into a single per-series verdict with a severity
classification. Stateless and deterministic: same input -> same output.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from src.logging import get_logger

from .schemas import AnomalyInfo, MetricResult

_log = get_logger(__name__)


@dataclass
class AnomalyDetectionConfig:
    """Tunable thresholds. Defaults are conservative (SAP: false positives are costly)."""

    z_score_threshold: float = 3.0
    z_score_enabled: bool = True

    moving_avg_window: int = 5
    moving_avg_deviation_threshold: float = 2.5
    moving_avg_enabled: bool = True

    threshold_rules: dict[str, dict[str, float]] = field(default_factory=dict)

    spike_ratio_threshold: float = 3.0
    spike_enabled: bool = True

    severity_thresholds: dict[str, int] = field(
        default_factory=lambda: {"low": 1, "medium": 3, "high": 5, "critical": 10}
    )

    min_data_points: int = 5


class AnomalyDetector:
    """Detects anomalies via Z-score, moving-average deviation, threshold, and spike methods."""

    def __init__(self, config: AnomalyDetectionConfig | None = None) -> None:
        self.config = config or AnomalyDetectionConfig()

    def detect_anomalies(
        self, result: MetricResult, threshold_rules: dict[str, float] | None = None
    ) -> MetricResult:
        """Populate ``result.anomalies`` with the union of all enabled detectors' findings."""
        if len(result.series) < self.config.min_data_points:
            result.anomalies = AnomalyInfo(detected=False, anomaly_count=0, severity="none")
            return result

        values = [p.value for p in result.series]

        z_score_anomalies: list[int] = []
        moving_avg_anomalies: list[int] = []
        threshold_anomalies: list[int] = []
        spike_anomalies: list[int] = []
        max_z_score = 0.0

        if self.config.z_score_enabled:
            z_score_anomalies, max_z_score = self._detect_z_score_anomalies(values)

        if self.config.moving_avg_enabled:
            moving_avg_anomalies = self._detect_moving_avg_anomalies(values)

        rules = threshold_rules or self.config.threshold_rules.get(result.metric)
        if rules:
            threshold_anomalies = self._detect_threshold_anomalies(values, rules)

        if self.config.spike_enabled:
            spike_anomalies = self._detect_spike_anomalies(values)

        all_anomaly_indices = set(
            z_score_anomalies + moving_avg_anomalies + threshold_anomalies + spike_anomalies
        )
        anomaly_count = len(all_anomaly_indices)

        result.anomalies = AnomalyInfo(
            detected=anomaly_count > 0,
            anomaly_count=anomaly_count,
            z_score_anomalies=len(z_score_anomalies),
            moving_avg_anomalies=len(moving_avg_anomalies),
            threshold_breaches=len(threshold_anomalies),
            max_z_score=max_z_score if max_z_score > 0 else None,
            severity=self._classify_severity(anomaly_count),
        )
        return result

    def detect_anomalies_batch(self, results: list[MetricResult]) -> list[MetricResult]:
        """Run :meth:`detect_anomalies` over every result."""
        return [self.detect_anomalies(result) for result in results]

    def _detect_z_score_anomalies(self, values: list[float]) -> tuple[list[int], float]:
        """Flag points whose |Z-score| exceeds the threshold; also return the max |Z| seen."""
        valid_values = [v for v in values if not math.isnan(v) and not math.isinf(v)]
        if len(valid_values) < 2:
            return [], 0.0

        mean = sum(valid_values) / len(valid_values)
        variance = sum((v - mean) ** 2 for v in valid_values) / len(valid_values)
        std_dev = math.sqrt(variance)
        if std_dev == 0:
            return [], 0.0

        anomalies: list[int] = []
        max_z_score = 0.0
        for i, value in enumerate(values):
            if math.isnan(value) or math.isinf(value):
                continue
            z_score = abs(value - mean) / std_dev
            max_z_score = max(max_z_score, z_score)
            if z_score > self.config.z_score_threshold:
                anomalies.append(i)
        return anomalies, max_z_score

    def _detect_moving_avg_anomalies(self, values: list[float]) -> list[int]:
        """Flag points deviating from the trailing moving-average window."""
        window = self.config.moving_avg_window
        if len(values) < window:
            return []

        anomalies: list[int] = []
        for i in range(window, len(values)):
            valid_window = [
                v for v in values[i - window : i] if not math.isnan(v) and not math.isinf(v)
            ]
            if not valid_window:
                continue

            moving_avg = sum(valid_window) / len(valid_window)
            moving_std = math.sqrt(
                sum((v - moving_avg) ** 2 for v in valid_window) / len(valid_window)
            )

            current_value = values[i]
            if math.isnan(current_value) or math.isinf(current_value):
                continue

            if moving_std > 0:
                deviation = abs(current_value - moving_avg) / moving_std
                if deviation > self.config.moving_avg_deviation_threshold:
                    anomalies.append(i)
            else:
                # A perfectly flat trailing window (moving_std == 0) is common for steady-state
                # SAP metrics. Require a materially large jump off the flat line, not floating-
                # point noise, so a 0.001% wobble is not reported as an anomaly.
                epsilon = max(abs(moving_avg) * 0.01, 1e-9)
                if abs(current_value - moving_avg) > epsilon:
                    anomalies.append(i)

        return anomalies

    def _detect_threshold_anomalies(
        self, values: list[float], rules: dict[str, float]
    ) -> list[int]:
        """Flag points breaching a static ``min``/``max`` rule."""
        anomalies: list[int] = []
        min_threshold = rules.get("min")
        max_threshold = rules.get("max")
        for i, value in enumerate(values):
            if math.isnan(value) or math.isinf(value):
                continue
            if min_threshold is not None and value < min_threshold:
                anomalies.append(i)
            if max_threshold is not None and value > max_threshold:
                anomalies.append(i)
        return anomalies

    def _detect_spike_anomalies(self, values: list[float]) -> list[int]:
        """Flag sudden large jumps between consecutive points, in EITHER direction.

        Uses the symmetric magnitude ratio ``max(|curr/prev|, |prev/curr|)`` so a collapse
        (e.g. 150 -> 1, an availability/throughput crash) is caught just like a surge — the
        asymmetric ``curr/prev`` alone would silently miss every downward drop.
        """
        anomalies: list[int] = []
        for i in range(1, len(values)):
            prev_value = values[i - 1]
            curr_value = values[i]
            if (
                math.isnan(prev_value)
                or math.isinf(prev_value)
                or math.isnan(curr_value)
                or math.isinf(curr_value)
            ):
                continue
            if prev_value == 0:
                if curr_value != 0:
                    anomalies.append(i)
                continue
            if curr_value == 0:
                # Drop to zero from a nonzero baseline is an unbounded ratio -> always a spike.
                anomalies.append(i)
                continue
            ratio = abs(curr_value / prev_value)
            symmetric_ratio = max(ratio, 1 / ratio)
            if symmetric_ratio > self.config.spike_ratio_threshold:
                anomalies.append(i)
        return anomalies

    def _classify_severity(self, anomaly_count: int) -> str:
        """Map an anomaly count to none/low/medium/high/critical."""
        if anomaly_count == 0:
            return "none"
        t = self.config.severity_thresholds
        if anomaly_count >= t.get("critical", 10):
            return "critical"
        if anomaly_count >= t.get("high", 5):
            return "high"
        if anomaly_count >= t.get("medium", 3):
            return "medium"
        return "low"

    def get_anomaly_summary(self, results: list[MetricResult]) -> dict[str, Any]:
        """Aggregate per-metric anomaly info into a ranked cross-metric summary."""
        total_anomalies = 0
        affected_metrics: list[dict[str, Any]] = []
        severity_counts = {"none": 0, "low": 0, "medium": 0, "high": 0, "critical": 0}

        for result in results:
            if result.anomalies:
                total_anomalies += result.anomalies.anomaly_count
                severity_counts[result.anomalies.severity] += 1
                if result.anomalies.detected:
                    affected_metrics.append(
                        {
                            "metric": result.metric,
                            "count": result.anomalies.anomaly_count,
                            "severity": result.anomalies.severity,
                            "max_z_score": result.anomalies.max_z_score,
                        }
                    )

        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        affected_metrics.sort(key=lambda x: (severity_order.get(x["severity"], 4), -x["count"]))

        return {
            "total_anomalies": total_anomalies,
            "metrics_affected": len(affected_metrics),
            "severity_distribution": severity_counts,
            "affected_metrics": affected_metrics,
            "overall_severity": self._get_overall_severity(severity_counts),
        }

    def _get_overall_severity(self, severity_counts: dict[str, int]) -> str:
        """Highest severity present across all metrics."""
        for level in ("critical", "high", "medium", "low"):
            if severity_counts.get(level, 0) > 0:
                return level
        return "none"

    def format_anomaly_report(self, results: list[MetricResult]) -> str:
        """Render the anomaly summary as a Markdown report for the explanation field."""
        summary = self.get_anomaly_summary(results)

        lines = [
            "## Anomaly Detection Report\n",
            f"**Overall Severity**: {summary['overall_severity'].upper()}",
            f"**Total Anomalies**: {summary['total_anomalies']}",
            f"**Metrics Affected**: {summary['metrics_affected']}\n",
        ]

        if summary["affected_metrics"]:
            lines.append("### Affected Metrics\n")
            for metric_info in summary["affected_metrics"]:
                z_score_str = ""
                if metric_info["max_z_score"]:
                    z_score_str = f" (max Z-score: {metric_info['max_z_score']:.2f})"
                lines.append(
                    f"- **{metric_info['metric']}**: {metric_info['count']} anomalies, "
                    f"severity: {metric_info['severity']}{z_score_str}"
                )
        else:
            lines.append("No anomalies detected in the analyzed metrics.")

        return "\n".join(lines)
