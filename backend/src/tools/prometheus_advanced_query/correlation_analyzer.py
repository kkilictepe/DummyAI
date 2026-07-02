"""Correlation analysis between metric time series (pure-python).

Ported from the reference ``correlation_analyzer.py`` with the numpy/scipy branch removed — the
platform ships no numpy in v1, so correlation uses the pure-python Pearson fallback
(coefficient + strength + interpretation; no p-value). Supports plain Pearson and time-shifted
cross-correlation (to find the lag that maximizes |r|). Deterministic given the same input.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from src.logging import get_logger

from .schemas import (
    CorrelationMethod,
    CorrelationResult,
    CorrelationStrength,
    MetricResult,
    MetricSeriesPoint,
)

_log = get_logger(__name__)


@dataclass
class CorrelationConfig:
    """Correlation thresholds and lag settings."""

    min_data_points: int = 10
    lag_step_seconds: int = 60
    default_max_lag: int = 300
    strong_threshold: float = 0.7
    moderate_threshold: float = 0.3


class CorrelationAnalyzer:
    """Computes Pearson / time-shifted correlation between metric series."""

    def __init__(self, config: CorrelationConfig | None = None) -> None:
        self.config = config or CorrelationConfig()

    def compute_correlation(
        self,
        reference_result: MetricResult,
        other_result: MetricResult,
        method: CorrelationMethod = CorrelationMethod.PEARSON,
        max_lag_seconds: int = 0,
    ) -> CorrelationResult:
        """Correlate ``other_result`` against ``reference_result`` using ``method``."""
        values_a, _values_b = self._align_series(reference_result.series, other_result.series)

        if len(values_a) < self.config.min_data_points:
            _log.warning(
                "correlation_insufficient_data",
                metric_a=reference_result.metric,
                metric_b=other_result.metric,
                aligned_points=len(values_a),
            )
            return CorrelationResult(
                metric_a=reference_result.metric,
                metric_b=other_result.metric,
                method=method,
                coefficient=0.0,
                lag_seconds=0,
                strength=CorrelationStrength.WEAK,
                p_value=None,
                interpretation="Insufficient data for correlation analysis",
            )

        if method == CorrelationMethod.PEARSON:
            return self._pearson_correlation(reference_result, other_result)
        return self._time_shifted_correlation(
            reference_result.metric,
            other_result.metric,
            reference_result.series,
            other_result.series,
            max_lag_seconds or self.config.default_max_lag,
        )

    def compute_all_correlations(
        self,
        results: list[MetricResult],
        reference_metric: str,
        method: CorrelationMethod = CorrelationMethod.PEARSON,
        max_lag_seconds: int = 0,
    ) -> list[CorrelationResult]:
        """Correlate every other metric against ``reference_metric``, strongest |r| first."""
        reference_result = next((r for r in results if r.metric == reference_metric), None)
        if reference_result is None:
            _log.error("correlation_reference_not_found", reference_metric=reference_metric)
            return []

        correlations = [
            self.compute_correlation(reference_result, result, method, max_lag_seconds)
            for result in results
            if result.metric != reference_metric
        ]
        correlations.sort(key=lambda c: abs(c.coefficient), reverse=True)
        return correlations

    def _pearson_correlation(
        self, reference_result: MetricResult, other_result: MetricResult
    ) -> CorrelationResult:
        values_a, values_b = self._align_series(reference_result.series, other_result.series)
        coefficient = self._pearson_pure_python(values_a, values_b)
        strength = self._classify_strength(coefficient)
        interpretation = self._interpret_correlation(
            reference_result.metric, other_result.metric, coefficient, strength, 0
        )
        return CorrelationResult(
            metric_a=reference_result.metric,
            metric_b=other_result.metric,
            method=CorrelationMethod.PEARSON,
            coefficient=round(coefficient, 4),
            lag_seconds=0,
            strength=strength,
            p_value=None,
            interpretation=interpretation,
        )

    def _time_shifted_correlation(
        self,
        metric_a: str,
        metric_b: str,
        series_a: list[MetricSeriesPoint],
        series_b: list[MetricSeriesPoint],
        max_lag_seconds: int,
    ) -> CorrelationResult:
        step_size = self._estimate_step_size(series_a) or self.config.lag_step_seconds
        max_lag_steps = max_lag_seconds // step_size
        lags_to_check = range(-max_lag_steps, max_lag_steps + 1)
        # Match a shifted b-point to an a-point within half a step. A smaller-than-spacing window
        # keeps the pairing one-to-one; the old fixed 30s tolerance collapsed multiple b-points
        # onto the same a-point whenever the step was <= 30s (e.g. a valid 15s step), fabricating
        # a nonzero best-lag even for two identical series.
        tolerance_seconds = max(step_size // 2, 1)

        best_coefficient = 0.0
        best_lag = 0
        for lag_steps in lags_to_check:
            lag_seconds = lag_steps * step_size
            values_a, values_b = self._align_with_lag(
                series_a, series_b, lag_seconds, tolerance_seconds
            )
            if len(values_a) < self.config.min_data_points:
                continue
            coef = self._pearson_pure_python(values_a, values_b)
            # Prefer a stronger |r|; on a tie prefer the smaller |lag| so a self-similar signal
            # (e.g. two identical series) reports lag 0 rather than an arbitrary tie-winner.
            if abs(coef) > abs(best_coefficient) or (
                abs(coef) == abs(best_coefficient) and abs(lag_seconds) < abs(best_lag)
            ):
                best_coefficient = coef
                best_lag = lag_seconds

        strength = self._classify_strength(best_coefficient)
        interpretation = self._interpret_correlation(
            metric_a, metric_b, best_coefficient, strength, best_lag
        )
        return CorrelationResult(
            metric_a=metric_a,
            metric_b=metric_b,
            method=CorrelationMethod.TIME_SHIFTED,
            coefficient=round(best_coefficient, 4),
            lag_seconds=best_lag,
            strength=strength,
            p_value=None,
            interpretation=interpretation,
        )

    @staticmethod
    def _pearson_pure_python(values_a: list[float], values_b: list[float]) -> float:
        """Pearson correlation coefficient. 0.0 when either series is constant or empty."""
        n = len(values_a)
        if n == 0:
            return 0.0

        mean_a = sum(values_a) / n
        mean_b = sum(values_b) / n
        cov = sum((a - mean_a) * (b - mean_b) for a, b in zip(values_a, values_b, strict=False)) / n
        std_a = math.sqrt(sum((a - mean_a) ** 2 for a in values_a) / n)
        std_b = math.sqrt(sum((b - mean_b) ** 2 for b in values_b) / n)
        if std_a == 0 or std_b == 0:
            return 0.0
        return cov / (std_a * std_b)

    @staticmethod
    def _align_series(
        series_a: list[MetricSeriesPoint], series_b: list[MetricSeriesPoint]
    ) -> tuple[list[float], list[float]]:
        """Align two series on common timestamps, dropping NaN/Inf pairs."""
        map_a = {p.timestamp: p.value for p in series_a}
        map_b = {p.timestamp: p.value for p in series_b}
        common_timestamps = sorted(set(map_a.keys()) & set(map_b.keys()))

        values_a: list[float] = []
        values_b: list[float] = []
        for ts in common_timestamps:
            val_a = map_a[ts]
            val_b = map_b[ts]
            if (
                not math.isnan(val_a)
                and not math.isinf(val_a)
                and not math.isnan(val_b)
                and not math.isinf(val_b)
            ):
                values_a.append(val_a)
                values_b.append(val_b)
        return values_a, values_b

    @staticmethod
    def _align_with_lag(
        series_a: list[MetricSeriesPoint],
        series_b: list[MetricSeriesPoint],
        lag_seconds: int,
        tolerance_seconds: int,
    ) -> tuple[list[float], list[float]]:
        """Align ``series_b`` shifted by ``lag_seconds`` onto ``series_a``, one-to-one.

        Each shifted b-point is matched to its NEAREST unconsumed a-point within
        ``tolerance_seconds`` (and each a-point is used at most once), so points cannot collapse
        many-to-one and corrupt the correlation.
        """
        a_points: list[tuple[float, float]] = []
        for p in series_a:
            if math.isnan(p.value) or math.isinf(p.value):
                continue
            try:
                ts = datetime.fromisoformat(p.timestamp.replace("Z", "+00:00")).timestamp()
            except ValueError:
                continue
            a_points.append((ts, p.value))
        a_points.sort()

        values_a: list[float] = []
        values_b: list[float] = []
        used: set[int] = set()

        for p in series_b:
            if math.isnan(p.value) or math.isinf(p.value):
                continue
            try:
                target_ts = (
                    datetime.fromisoformat(p.timestamp.replace("Z", "+00:00")).timestamp()
                    - lag_seconds
                )
            except ValueError:
                continue

            best_idx: int | None = None
            best_dist = float(tolerance_seconds) + 1.0
            for idx, (ts_a, _val_a) in enumerate(a_points):
                if idx in used:
                    continue
                dist = abs(ts_a - target_ts)
                if dist <= tolerance_seconds and dist < best_dist:
                    best_dist = dist
                    best_idx = idx

            if best_idx is not None:
                used.add(best_idx)
                values_a.append(a_points[best_idx][1])
                values_b.append(p.value)

        return values_a, values_b

    @staticmethod
    def _estimate_step_size(series: list[MetricSeriesPoint]) -> int:
        """Estimate series resolution (seconds) from the first two timestamps; 0 if unknown."""
        if len(series) < 2:
            return 0
        try:
            dt0 = datetime.fromisoformat(series[0].timestamp.replace("Z", "+00:00"))
            dt1 = datetime.fromisoformat(series[1].timestamp.replace("Z", "+00:00"))
            return int(abs((dt1 - dt0).total_seconds()))
        except (ValueError, AttributeError):
            return 0

    def _classify_strength(self, coefficient: float) -> CorrelationStrength:
        """weak (|r|<0.3) / moderate (0.3<=|r|<0.7) / strong (|r|>=0.7)."""
        abs_coef = abs(coefficient)
        if abs_coef >= self.config.strong_threshold:
            return CorrelationStrength.STRONG
        if abs_coef >= self.config.moderate_threshold:
            return CorrelationStrength.MODERATE
        return CorrelationStrength.WEAK

    def _interpret_correlation(
        self,
        metric_a: str,
        metric_b: str,
        coefficient: float,
        strength: CorrelationStrength,
        lag_seconds: int,
    ) -> str:
        """Human-readable interpretation, including lead/lag and RCA hints for strong links."""
        direction = "positive" if coefficient > 0 else "negative"

        if strength == CorrelationStrength.WEAK:
            relationship = "little to no linear relationship"
        elif strength == CorrelationStrength.MODERATE:
            relationship = f"a moderate {direction} relationship"
        else:
            relationship = f"a strong {direction} relationship"

        interpretation = f"{metric_a} and {metric_b} show {relationship}"

        if lag_seconds > 0:
            interpretation += f". {metric_a} leads {metric_b} by {lag_seconds} seconds"
        elif lag_seconds < 0:
            interpretation += f". {metric_b} leads {metric_a} by {abs(lag_seconds)} seconds"

        if strength == CorrelationStrength.STRONG:
            if coefficient > 0:
                interpretation += (
                    ". This suggests these metrics may share a common cause or one may be "
                    "driving the other."
                )
            else:
                interpretation += (
                    ". This inverse relationship suggests a resource contention or trade-off "
                    "pattern."
                )

        return interpretation

    def format_correlation_report(
        self, correlations: list[CorrelationResult], reference_metric: str
    ) -> str:
        """Render correlations grouped by strength as a Markdown report."""
        lines = ["## Correlation Analysis Report", f"\n**Reference Metric**: {reference_metric}\n"]

        strong = [c for c in correlations if c.strength == CorrelationStrength.STRONG]
        moderate = [c for c in correlations if c.strength == CorrelationStrength.MODERATE]
        weak = [c for c in correlations if c.strength == CorrelationStrength.WEAK]

        if strong:
            lines.append("### Strong Correlations\n")
            for c in strong:
                lag_info = f" (lag: {c.lag_seconds}s)" if c.lag_seconds != 0 else ""
                lines.append(f"- **{c.metric_b}**: r = {c.coefficient:.3f}{lag_info}")
                lines.append(f"  - {c.interpretation}")

        if moderate:
            lines.append("\n### Moderate Correlations\n")
            for c in moderate:
                lag_info = f" (lag: {c.lag_seconds}s)" if c.lag_seconds != 0 else ""
                lines.append(f"- {c.metric_b}: r = {c.coefficient:.3f}{lag_info}")

        if weak:
            lines.append("\n### Weak/No Correlations\n")
            lines.append(f"- {len(weak)} metrics showed weak or no correlation")

        if not correlations:
            lines.append("No correlation analysis results available.")

        return "\n".join(lines)
