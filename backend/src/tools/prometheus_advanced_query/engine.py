"""Query engine for the advanced Prometheus tool.

Holds the stateless sub-components (validator, PromQL builder, normalizer, anomaly detector,
correlation analyzer) and implements the five query strategies. This replaces the reference's
``BaseTool`` subclass + its ``_run`` ThreadPoolExecutor hack: the engine is a plain async object
and the Prometheus client is passed in per call (Dummy AI has one shared client — no per-system
routing). Guardrail limits come from ``tools.yaml`` via ``AdvancedQueryConfig.from_tools_config``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from time import perf_counter
from typing import TYPE_CHECKING

from src.config import get_tools_config
from src.logging import get_logger

from .anomaly_detector import AnomalyDetectionConfig, AnomalyDetector
from .correlation_analyzer import CorrelationAnalyzer, CorrelationConfig
from .normalizer import ResultNormalizer
from .promql_builder import SafePromQLBuilder
from .schemas import (
    BaselineComparisonResult,
    MetricResult,
    PrometheusAdvanceQueryInput,
    PrometheusAdvanceQueryOutput,
    QueryType,
    TimeRangeInput,
)
from .validation import GuardrailConfig, InputValidator

if TYPE_CHECKING:
    from src.clients.prometheus import PrometheusClient

_log = get_logger(__name__)

_TOOL_CONFIG_KEY = "prometheus_advance_query"


@dataclass(frozen=True)
class AdvancedQueryConfig:
    """Guardrail + analysis knobs for the tool (sourced from ``tools.yaml``)."""

    max_metrics_per_call: int = 10
    max_time_range_hours: int = 24
    max_data_points: int = 10000
    z_score_threshold: float = 3.0
    enable_anomaly_detection: bool = True
    min_correlation_points: int = 10
    default_baseline_offset_hours: int = 24

    @classmethod
    def from_tools_config(cls) -> AdvancedQueryConfig:
        """Build from the ``prometheus_advance_query`` block of ``tools.yaml``."""
        raw = get_tools_config().for_tool(_TOOL_CONFIG_KEY)
        return cls(
            max_metrics_per_call=int(raw.get("max_metrics_per_call", 10)),
            max_time_range_hours=int(raw.get("max_time_range_hours", 24)),
            max_data_points=int(raw.get("max_data_points", 10000)),
            z_score_threshold=float(raw.get("z_score_threshold", 3.0)),
            enable_anomaly_detection=bool(raw.get("enable_anomaly_detection", True)),
            min_correlation_points=int(raw.get("min_correlation_points", 10)),
            default_baseline_offset_hours=int(raw.get("default_baseline_offset_hours", 24)),
        )


class QueryEngine:
    """Executes instant/range/anomaly/baseline/correlation strategies over a Prometheus client."""

    def __init__(self, config: AdvancedQueryConfig) -> None:
        self._config = config
        self._validator = InputValidator(
            GuardrailConfig(
                max_metrics_per_call=config.max_metrics_per_call,
                max_time_range_hours=config.max_time_range_hours,
                max_data_points_per_series=config.max_data_points,
            )
        )
        self._promql = SafePromQLBuilder()
        self._normalizer = ResultNormalizer()
        self._anomaly = AnomalyDetector(
            AnomalyDetectionConfig(z_score_threshold=config.z_score_threshold)
        )
        self._correlation = CorrelationAnalyzer(
            CorrelationConfig(min_data_points=config.min_correlation_points)
        )

    async def run(
        self, input_data: PrometheusAdvanceQueryInput, client: PrometheusClient
    ) -> PrometheusAdvanceQueryOutput:
        """Validate then dispatch to the strategy for ``input_data.query_type``.

        Never raises: guardrail failures become a structured error result, and any unexpected
        exception is logged server-side and returned as a generic error (its text is not exposed
        to the caller, which may be relayed to the browser via the AG-UI tool-result event).
        """
        start_time = perf_counter()
        try:
            is_valid, error_msg = self._validator.validate(input_data)
            if not is_valid:
                return self._error_response(error_msg or "Validation failed", start_time)

            query_type = input_data.query_type
            if query_type == QueryType.INSTANT:
                result = await self._execute_instant_query(input_data, client)
            elif query_type == QueryType.RANGE:
                result = await self._execute_range_query(input_data, client)
            elif query_type == QueryType.ANOMALY_CHECK:
                result = await self._execute_anomaly_check(input_data, client)
            elif query_type == QueryType.BASELINE_COMPARE:
                result = await self._execute_baseline_compare(input_data, client)
            elif query_type == QueryType.CORRELATION:
                result = await self._execute_correlation(input_data, client)
            else:  # pragma: no cover - QueryType is exhaustive
                return self._error_response(f"Unknown query type: {query_type}", start_time)

            result.metadata["execution_time_ms"] = round((perf_counter() - start_time) * 1000, 2)
            return result
        except Exception:
            _log.exception("advanced_query_unexpected_error")
            return self._error_response(
                "Internal error while executing the advanced query.", start_time
            )

    # ------------------------------------------------------------------
    # Strategies
    # ------------------------------------------------------------------

    async def _execute_instant_query(
        self, input_data: PrometheusAdvanceQueryInput, client: PrometheusClient
    ) -> PrometheusAdvanceQueryOutput:
        results: list[MetricResult] = []
        for metric_name, query in self._promql.build_multi_metric_query(
            input_data.metric_names, input_data.labels
        ):
            response = await client.instant_query(query, time=input_data.time_range.end)
            if response.success:
                results.extend(self._normalizer.normalize_response(response, metric_name))
            else:
                _log.warning(
                    "advanced_query_metric_failed",
                    metric=metric_name,
                    error=response.error_message,
                )

        return PrometheusAdvanceQueryOutput(
            status="success",
            query_type=input_data.query_type,
            time_range=self._time_range_dict(input_data.time_range),
            results=results,
            explanation=self._generate_explanation(input_data, results),
            metadata={"metrics_queried": len(input_data.metric_names)},
        )

    async def _execute_range_query(
        self, input_data: PrometheusAdvanceQueryInput, client: PrometheusClient
    ) -> PrometheusAdvanceQueryOutput:
        queries = self._promql.build_multi_metric_query(input_data.metric_names, input_data.labels)
        response_map = await client.query_multiple(
            queries,
            input_data.time_range.start,
            input_data.time_range.end,
            input_data.time_range.step,
        )

        results: list[MetricResult] = []
        for metric_name, response in response_map.items():
            if not response.success:
                _log.warning(
                    "advanced_query_metric_failed",
                    metric=metric_name,
                    error=response.error_message,
                )
                continue
            # Merge multiple app-server series on RAW values (before NaN/Inf coercion) so a
            # missing scrape on one server does not get averaged in as a real 0.0.
            series_list = self._normalizer.parse_series(response, metric_name)
            if len(series_list) > 1:
                merged = self._normalizer.merge_metric_data(series_list, metric_name)
                results.append(self._normalizer.normalize_metric_data(merged))
            else:
                results.extend(self._normalizer.normalize_metric_data(md) for md in series_list)

        return PrometheusAdvanceQueryOutput(
            status="success",
            query_type=input_data.query_type,
            time_range=self._time_range_dict(input_data.time_range),
            results=results,
            explanation=self._generate_explanation(input_data, results),
            metadata={
                "metrics_queried": len(input_data.metric_names),
                "series_returned": len(results),
            },
        )

    async def _execute_anomaly_check(
        self, input_data: PrometheusAdvanceQueryInput, client: PrometheusClient
    ) -> PrometheusAdvanceQueryOutput:
        range_result = await self._execute_range_query(input_data, client)
        if range_result.status != "success":
            return range_result

        if self._config.enable_anomaly_detection:
            range_result.results = self._anomaly.detect_anomalies_batch(range_result.results)

        range_result.anomalies_detected = any(
            r.anomalies and r.anomalies.detected for r in range_result.results
        )

        anomaly_summary = self._anomaly.get_anomaly_summary(range_result.results)
        if anomaly_summary["total_anomalies"] > 0:
            severity_confidence = {
                "critical": 0.95,
                "high": 0.85,
                "medium": 0.75,
                "low": 0.6,
                "none": 1.0,
            }
            range_result.overall_confidence = severity_confidence.get(
                anomaly_summary["overall_severity"], 0.7
            )

        range_result.rca_insights = self._generate_rca_insights(
            range_result.results, anomaly_summary
        )
        range_result.explanation = (
            f"{range_result.explanation}\n\n"
            f"{self._anomaly.format_anomaly_report(range_result.results)}"
        )
        range_result.metadata["anomaly_summary"] = anomaly_summary
        return range_result

    async def _execute_baseline_compare(
        self, input_data: PrometheusAdvanceQueryInput, client: PrometheusClient
    ) -> PrometheusAdvanceQueryOutput:
        current_start = datetime.fromisoformat(input_data.time_range.start.replace("Z", "+00:00"))
        current_end = datetime.fromisoformat(input_data.time_range.end.replace("Z", "+00:00"))
        baseline_offset = timedelta(hours=self._config.default_baseline_offset_hours)

        current_output = await self._execute_range_query(input_data, client)
        if current_output.status != "success":
            return current_output

        baseline_input = PrometheusAdvanceQueryInput(
            metric_names=input_data.metric_names,
            time_range=TimeRangeInput(
                start=(current_start - baseline_offset).isoformat(),
                end=(current_end - baseline_offset).isoformat(),
                step=input_data.time_range.step,
            ),
            query_type=QueryType.RANGE,
            labels=input_data.labels,
        )
        baseline_output = await self._execute_range_query(baseline_input, client)

        baseline_results: list[BaselineComparisonResult] = []
        for current_result in current_output.results:
            baseline_result = next(
                (b for b in baseline_output.results if b.metric == current_result.metric), None
            )
            if baseline_result is not None:
                baseline_results.append(self._compare_to_baseline(current_result, baseline_result))

        if self._config.enable_anomaly_detection:
            current_output.results = self._anomaly.detect_anomalies_batch(current_output.results)
        current_output.explanation = (
            f"{current_output.explanation}\n\n"
            f"{self._generate_baseline_explanation(baseline_results, baseline_offset)}"
        )
        current_output.baseline_comparisons = baseline_results
        current_output.query_type = QueryType.BASELINE_COMPARE

        significant_deviations = [b for b in baseline_results if b.is_significant]
        if significant_deviations:
            current_output.anomalies_detected = True
            current_output.rca_insights = [
                f"Significant deviation from baseline detected for {b.metric}: "
                f"{b.deviation_percent:+.1f}% change"
                for b in significant_deviations
            ]

        current_output.metadata["baseline_offset_hours"] = (
            self._config.default_baseline_offset_hours
        )
        current_output.metadata["baseline_comparisons_count"] = len(baseline_results)
        return current_output

    async def _execute_correlation(
        self, input_data: PrometheusAdvanceQueryInput, client: PrometheusClient
    ) -> PrometheusAdvanceQueryOutput:
        range_result = await self._execute_range_query(input_data, client)
        if range_result.status != "success":
            return range_result

        correlation_params = input_data.correlation
        if correlation_params is None:  # pragma: no cover - guarded by validation
            return self._error_response("Correlation parameters required", perf_counter())

        correlations = self._correlation.compute_all_correlations(
            range_result.results,
            correlation_params.reference_metric,
            correlation_params.method,
            correlation_params.max_lag_seconds,
        )
        range_result.correlation_results = correlations
        if self._config.enable_anomaly_detection:
            range_result.results = self._anomaly.detect_anomalies_batch(range_result.results)
        report = self._correlation.format_correlation_report(
            correlations, correlation_params.reference_metric
        )
        range_result.explanation = f"{range_result.explanation}\n\n{report}"

        strong_correlations = [c for c in correlations if c.strength.value == "strong"]
        if strong_correlations:
            range_result.rca_insights = [c.interpretation for c in strong_correlations]

        range_result.query_type = QueryType.CORRELATION
        range_result.metadata["correlation_method"] = correlation_params.method.value
        range_result.metadata["correlations_computed"] = len(correlations)
        return range_result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _compare_to_baseline(
        self, current: MetricResult, baseline: MetricResult
    ) -> BaselineComparisonResult:
        current_avg = current.summary.avg
        baseline_avg = baseline.summary.avg

        if baseline_avg == 0:
            deviation_percent = 0.0 if current_avg == 0 else 100.0
        else:
            deviation_percent = ((current_avg - baseline_avg) / baseline_avg) * 100

        abs_deviation = abs(deviation_percent)
        if abs_deviation < 10:
            severity, is_significant = "normal", False
        elif abs_deviation < 25:
            severity, is_significant = "elevated", False
        elif abs_deviation < 50:
            severity, is_significant = "warning", True
        else:
            severity, is_significant = "critical", True

        direction = "increased" if deviation_percent > 0 else "decreased"
        interpretation = (
            f"{current.metric} has {direction} by {abs(deviation_percent):.1f}% "
            f"compared to the baseline period. "
        )
        interpretation += (
            "This is a significant deviation that warrants investigation."
            if is_significant
            else "This is within normal variation."
        )

        return BaselineComparisonResult(
            metric=current.metric,
            current_avg=round(current_avg, 4),
            baseline_avg=round(baseline_avg, 4),
            deviation_percent=round(deviation_percent, 2),
            is_significant=is_significant,
            severity=severity,
            interpretation=interpretation,
        )

    @staticmethod
    def _time_range_dict(time_range: TimeRangeInput) -> dict[str, str]:
        return {"start": time_range.start, "end": time_range.end, "step": time_range.step}

    def _generate_explanation(
        self, input_data: PrometheusAdvanceQueryInput, results: list[MetricResult]
    ) -> str:
        lines = [
            "## Query Analysis\n",
            f"**Query Type**: {input_data.query_type.value}",
            f"**Metrics Queried**: {', '.join(input_data.metric_names)}",
            f"**Time Range**: {input_data.time_range.start} to {input_data.time_range.end}",
        ]
        if input_data.labels:
            label_str = ", ".join(f"{k}={v}" for k, v in input_data.labels.items())
            lines.append(f"**Label Filters**: {label_str}")
        lines.append("")

        if results:
            lines.append("## Results Summary\n")
            for result in results:
                lines.append(f"### {result.metric}")
                lines.append(f"- Data points: {result.summary.data_points}")
                lines.append(f"- Range: {result.summary.min:.2f} to {result.summary.max:.2f}")
                lines.append(f"- Average: {result.summary.avg:.2f}")
                lines.append(f"- P95: {result.summary.p95:.2f}")
                lines.append(f"- Trend: {result.summary.trend.value}")
                lines.append("")
        else:
            lines.append("No data returned for the specified metrics and time range.")

        return "\n".join(lines)

    def _generate_baseline_explanation(
        self, comparisons: list[BaselineComparisonResult], offset: timedelta
    ) -> str:
        lines = [
            "## Baseline Comparison\n",
            f"**Baseline Period**: {offset.total_seconds() / 3600:.0f} hours ago\n",
        ]
        if not comparisons:
            lines.append("No baseline comparisons available.")
            return "\n".join(lines)

        significant = [c for c in comparisons if c.is_significant]
        normal = [c for c in comparisons if not c.is_significant]

        if significant:
            lines.append("### Significant Deviations\n")
            for c in significant:
                lines.append(
                    f"- **{c.metric}**: {c.deviation_percent:+.1f}% "
                    f"(current: {c.current_avg:.2f}, baseline: {c.baseline_avg:.2f})"
                )
            lines.append("")
        if normal:
            lines.append("### Within Normal Range\n")
            lines.append(f"- {len(normal)} metrics are within normal baseline deviation")

        return "\n".join(lines)

    def _generate_rca_insights(
        self, results: list[MetricResult], anomaly_summary: dict[str, object]
    ) -> list[str]:
        insights: list[str] = []
        overall_severity = str(anomaly_summary["overall_severity"])
        affected_metrics = anomaly_summary["affected_metrics"]

        if overall_severity in ("critical", "high"):
            insights.append(
                f"ALERT: {overall_severity.upper()} severity anomalies detected across "
                f"{anomaly_summary['metrics_affected']} metrics"
            )

        assert isinstance(affected_metrics, list)
        for metric_info in affected_metrics[:3]:
            if metric_info["severity"] in ("critical", "high"):
                insights.append(
                    f"Investigate {metric_info['metric']}: "
                    f"{metric_info['count']} anomalous data points detected"
                )

        for result in results:
            if not (result.anomalies and result.anomalies.detected):
                continue
            if result.summary.trend.value == "up":
                insights.append(
                    f"{result.metric} shows upward trend with anomalies - "
                    "potential resource exhaustion or load increase"
                )
            elif result.summary.trend.value == "down":
                insights.append(
                    f"{result.metric} shows downward trend with anomalies - "
                    "potential service degradation or capacity issue"
                )

        return insights

    def _error_response(
        self, error_message: str, start_time: float
    ) -> PrometheusAdvanceQueryOutput:
        return PrometheusAdvanceQueryOutput(
            status="error",
            query_type=QueryType.RANGE,
            time_range={"start": "", "end": "", "step": ""},
            results=[],
            explanation=f"Query failed: {error_message}",
            error=error_message,
            metadata={"execution_time_ms": round((perf_counter() - start_time) * 1000, 2)},
        )


def build_query_engine() -> QueryEngine:
    """Construct a :class:`QueryEngine` from the committed ``tools.yaml`` configuration."""
    return QueryEngine(AdvancedQueryConfig.from_tools_config())
