"""Normalize raw Prometheus responses into structured ``MetricResult`` models + statistics.

Ported from the reference ``normalizer.py`` — the only substantive edit is the import root
(``src.clients.prometheus``). Keeps its own inlined ``_parse_metric_data`` so the normalizer has
no runtime dependency on a live client. Converts timestamps to ISO-8601, handles NaN/Inf, and
computes min/max/avg/p95 + a linear-regression trend. Stateless and deterministic.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

from src.clients.prometheus import MetricData, PrometheusResponse
from src.logging import get_logger

from .schemas import MetricResult, MetricSeriesPoint, MetricSummary, TrendDirection

_log = get_logger(__name__)


class ResultNormalizer:
    """Turns Prometheus responses into ``MetricResult`` objects with summary statistics."""

    def normalize_metric_data(
        self, metric_data: MetricData, include_labels: bool = True
    ) -> MetricResult:
        """Normalize one series into a ``MetricResult`` (anomalies filled in later)."""
        series = self._build_series_points(metric_data.values)
        valid_values = [v for _, v in metric_data.values if not math.isnan(v) and not math.isinf(v)]
        summary = self._compute_summary(valid_values, metric_data.values)

        return MetricResult(
            metric=metric_data.metric_name,
            labels=metric_data.labels if include_labels else None,
            series=series,
            summary=summary,
            anomalies=None,
        )

    def parse_series(
        self, response: PrometheusResponse, metric_name: str | None = None
    ) -> list[MetricData]:
        """Parse a response into raw ``MetricData`` series, overriding the name when given.

        Exposes the pre-coercion values so callers (e.g. the range strategy's multi-series merge)
        can aggregate on genuine samples rather than on NaN-coerced output points.
        """
        if not response.success or not response.data:
            _log.warning("normalize_failed_response", error=response.error_message)
            return []

        parsed = self._parse_metric_data(response)
        if metric_name:
            for metric_data in parsed:
                metric_data.metric_name = metric_name
        return parsed

    def normalize_response(
        self, response: PrometheusResponse, metric_name: str | None = None
    ) -> list[MetricResult]:
        """Normalize a full response into per-series ``MetricResult`` objects."""
        return [self.normalize_metric_data(md) for md in self.parse_series(response, metric_name)]

    @staticmethod
    def _parse_metric_data(response: PrometheusResponse) -> list[MetricData]:
        """Parse a raw response into ``MetricData`` (inlined so no live client is needed)."""
        if not response.success or not response.data:
            return []

        result_type = response.data.get("resultType", "")
        results = response.data.get("result", [])
        out: list[MetricData] = []

        for r in results:
            if not isinstance(r, dict):
                continue
            metric_info = r.get("metric", {})
            metric_name = metric_info.get("__name__", "unknown")
            labels = {k: v for k, v in metric_info.items() if k != "__name__"}

            if result_type == "matrix":
                values = [
                    (float(v[0]), float(v[1]) if v[1] != "NaN" else float("nan"))
                    for v in r.get("values", [])
                ]
            elif result_type == "vector":
                v = r.get("value", [0, "0"])
                values = [(float(v[0]), float(v[1]) if v[1] != "NaN" else float("nan"))]
            else:
                values = []

            out.append(MetricData(metric_name=metric_name, labels=labels, values=values))

        return out

    def _build_series_points(self, values: list[tuple[float, float]]) -> list[MetricSeriesPoint]:
        """Convert ``(unix_ts, value)`` pairs to ISO-8601 points.

        Non-finite samples (NaN, +/-Inf — routine for Prometheus: staleness, div-by-zero,
        ``histogram_quantile`` on empty buckets) are coerced to ``0.0``. This keeps the emitted
        series JSON-valid: they are surfaced verbatim to the browser via the AG-UI tool-result
        event, and ``Infinity``/``NaN`` are not legal JSON tokens.
        """
        series: list[MetricSeriesPoint] = []
        for timestamp, value in values:
            try:
                iso_timestamp = datetime.fromtimestamp(timestamp, tz=UTC).isoformat()
            except (ValueError, OSError, OverflowError):
                iso_timestamp = f"invalid:{timestamp}"

            if not math.isfinite(value):
                value = 0.0

            series.append(MetricSeriesPoint(timestamp=iso_timestamp, value=value))

        return series

    def _compute_summary(
        self, values: list[float], raw_values: list[tuple[float, float]]
    ) -> MetricSummary:
        """Compute min/max/avg/p95 and a trend direction (empty -> all-zero flat summary).

        Defensively filters non-finite values so no NaN/Inf can reach the (JSON-serialized,
        browser-visible) summary even if a caller passes raw values.
        """
        finite = [v for v in values if math.isfinite(v)]
        if not finite:
            return MetricSummary(
                min=0.0, max=0.0, avg=0.0, p95=0.0, trend=TrendDirection.FLAT, data_points=0
            )

        return MetricSummary(
            min=round(min(finite), 6),
            max=round(max(finite), 6),
            avg=round(sum(finite) / len(finite), 6),
            p95=round(self._calculate_percentile(finite, 95), 6),
            trend=self._detect_trend(raw_values),
            data_points=len(finite),
        )

    def _calculate_percentile(self, values: list[float], percentile: int) -> float:
        """nth percentile with linear interpolation between neighbouring ranks."""
        if not values:
            return 0.0

        sorted_values = sorted(values)
        n = len(sorted_values)
        if n == 1:
            return sorted_values[0]

        pos = (percentile / 100) * (n - 1)
        lower_idx = int(pos)
        upper_idx = min(lower_idx + 1, n - 1)
        weight = pos - lower_idx
        return sorted_values[lower_idx] * (1 - weight) + sorted_values[upper_idx] * weight

    def _detect_trend(
        self, raw_values: list[tuple[float, float]], threshold: float = 0.1
    ) -> TrendDirection:
        """Classify the least-squares slope (normalized by value range) as up / down / flat."""
        if len(raw_values) < 2:
            return TrendDirection.FLAT

        valid_data = [(t, v) for t, v in raw_values if not math.isnan(v) and not math.isinf(v)]
        if len(valid_data) < 2:
            return TrendDirection.FLAT

        n = len(valid_data)
        timestamps = [t for t, _ in valid_data]
        values = [v for _, v in valid_data]

        t_min, t_max = min(timestamps), max(timestamps)
        t_range = t_max - t_min
        if t_range == 0:
            return TrendDirection.FLAT

        norm_timestamps = [(t - t_min) / t_range for t in timestamps]
        t_mean = sum(norm_timestamps) / n
        v_mean = sum(values) / n

        numerator = sum(
            (t - t_mean) * (v - v_mean) for t, v in zip(norm_timestamps, values, strict=False)
        )
        denominator = sum((t - t_mean) ** 2 for t in norm_timestamps)
        if denominator == 0:
            return TrendDirection.FLAT

        slope = numerator / denominator
        v_range = max(values) - min(values)
        if v_range == 0:
            return TrendDirection.FLAT

        normalized_slope = slope / v_range
        if normalized_slope > threshold:
            return TrendDirection.UP
        if normalized_slope < -threshold:
            return TrendDirection.DOWN
        return TrendDirection.FLAT

    def merge_metric_data(self, series_list: list[MetricData], metric_name: str) -> MetricData:
        """Merge multiple raw series (different label sets) into one, averaging per timestamp.

        Averages only **finite** samples at each timestamp: a NaN/Inf (a missing scrape on one
        app server) is skipped rather than counted as a real ``0.0``, so a single server's gap
        no longer drags the mean toward zero. A timestamp missing from every series is dropped
        rather than fabricated. Order-independent (sorted by timestamp), so a multi-app-server
        query is deterministic regardless of Prometheus's series ordering.
        """
        if len(series_list) == 1:
            return series_list[0]

        timestamp_values: dict[float, list[float]] = {}
        for metric_data in series_list:
            for timestamp, value in metric_data.values:
                if math.isfinite(value):
                    timestamp_values.setdefault(timestamp, []).append(value)

        merged_values = [
            (timestamp, sum(vals) / len(vals))
            for timestamp, vals in sorted(timestamp_values.items())
        ]

        return MetricData(
            metric_name=metric_name,
            labels={"_merged": "true", "_series_count": str(len(series_list))},
            values=merged_values,
        )
