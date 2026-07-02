"""Pydantic schemas + enums for the Prometheus advanced-query tool.

Ported near-verbatim from the reference tool's ``schemas.py`` (modernized typing for the repo's
ruff/mypy-strict gate). Strict, schema-first input validation: only pre-approved metric names,
no raw PromQL, simple label filters, correlation requires explicit parameters. The rich output
models double as the RCA-ready result shape.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

# =============================================================================
# ENUMERATIONS
# =============================================================================


class QueryType(StrEnum):
    """Supported query types.

    - ``instant``: single point-in-time query
    - ``range``: time series over a window
    - ``anomaly_check``: range query with anomaly detection
    - ``baseline_compare``: compare current window to historical baseline
    - ``correlation``: compute correlation between metrics
    """

    INSTANT = "instant"
    RANGE = "range"
    ANOMALY_CHECK = "anomaly_check"
    BASELINE_COMPARE = "baseline_compare"
    CORRELATION = "correlation"


class CorrelationMethod(StrEnum):
    """Correlation computation methods.

    - ``pearson``: standard Pearson correlation coefficient (no time shift)
    - ``time_shifted``: correlation at multiple time lags to find the best match
    """

    PEARSON = "pearson"
    TIME_SHIFTED = "time_shifted"


class TrendDirection(StrEnum):
    """Trend direction for time-series analysis."""

    UP = "up"
    DOWN = "down"
    FLAT = "flat"


class CorrelationStrength(StrEnum):
    """Correlation strength: weak (|r|<0.3), moderate (0.3<=|r|<0.7), strong (|r|>=0.7)."""

    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"


# =============================================================================
# INPUT SCHEMAS
# =============================================================================

_STEP_RE = re.compile(r"^\d+[smh]$")
_METRIC_NAME_RE = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")


class TimeRangeInput(BaseModel):
    """Time range specification (ISO-8601 start/end + resolution step)."""

    start: str = Field(
        ..., description="Start time in ISO-8601 format (e.g., '2024-01-20T10:00:00Z')"
    )
    end: str = Field(..., description="End time in ISO-8601 format (e.g., '2024-01-20T11:00:00Z')")
    step: str = Field(..., description="Query resolution step (e.g., '15s', '60s', '5m')")

    @field_validator("step")
    @classmethod
    def validate_step(cls, v: str) -> str:
        """Validate step format (number followed by s/m/h)."""
        if not _STEP_RE.match(v):
            raise ValueError(
                f"Invalid step format: {v}. Expected format: <number><s|m|h> (e.g., '15s', '5m')"
            )
        return v


class CorrelationInput(BaseModel):
    """Correlation analysis parameters. Only used when ``query_type`` is ``correlation``."""

    method: CorrelationMethod = Field(
        description="Correlation method: 'pearson' or 'time_shifted'",
    )
    reference_metric: str = Field(
        description="Reference metric name (must exist in metric_names list)"
    )
    max_lag_seconds: int = Field(
        description=(
            "Maximum lag in seconds for time-shifted correlation (only used for the "
            "'time_shifted' method). Use 300 as default. Range: 0-3600."
        ),
        ge=0,
        le=3600,
    )


class PrometheusAdvanceQueryInput(BaseModel):
    """Complete, safety-first input schema.

    Only pre-approved metric names are accepted, no raw PromQL, simple label filters only, and
    correlation requires explicit parameters. Guardrails: max 10 metrics, max 24h range,
    validated steps.
    """

    metric_names: list[str] = Field(
        ...,
        description="List of Prometheus metric names (max 10, pre-approved by metric_lookup)",
        min_length=1,
        max_length=10,
    )
    time_range: TimeRangeInput = Field(
        ..., description="Time range specification with start, end, and step"
    )
    query_type: QueryType = Field(
        default=QueryType.RANGE,
        description="Query type: instant, range, anomaly_check, baseline_compare, or correlation",
    )
    labels: dict[str, str] | None = Field(
        default=None,
        description="Label filters as key-value pairs (e.g., {'system_id': 'KHP', 'instance': "
        "'app01'})",
    )
    correlation: CorrelationInput | None = Field(
        default=None,
        description="Correlation parameters (required when query_type is 'correlation')",
    )
    system_id: str | None = Field(
        default=None,
        description=(
            "Optional SAP system id to scope the query to (added as the 'system_id' PromQL "
            "label, e.g. 'KHP'). Takes precedence over any system_id given in 'labels'."
        ),
    )

    @field_validator("metric_names")
    @classmethod
    def validate_metric_names(cls, v: list[str]) -> list[str]:
        """De-duplicate (order-preserving) and reject empty / injection-prone metric names."""
        seen: set[str] = set()
        unique_metrics: list[str] = []
        for metric in v:
            if metric not in seen:
                seen.add(metric)
                unique_metrics.append(metric)

        for metric in unique_metrics:
            if not metric or not metric.strip():
                raise ValueError("Empty metric name not allowed")
            if not _METRIC_NAME_RE.match(metric):
                raise ValueError(
                    f"Invalid metric name: '{metric}'. Metric names must start with a letter, "
                    "underscore, or colon, and contain only alphanumeric characters, "
                    "underscores, and colons."
                )

        return unique_metrics

    @model_validator(mode="after")
    def _check_correlation_consistency(self) -> PrometheusAdvanceQueryInput:
        """Correlation params are required for — and only valid for — ``correlation`` queries.

        A model validator (not a field validator) so the "required" case fires even when
        ``correlation`` is omitted: a field validator would be skipped for the default ``None``.
        """
        if self.query_type == QueryType.CORRELATION and self.correlation is None:
            raise ValueError(
                "correlation parameter is required when query_type is 'correlation'. "
                "Provide method, reference_metric, and max_lag_seconds."
            )

        if self.query_type != QueryType.CORRELATION and self.correlation is not None:
            raise ValueError(
                "correlation parameter is only valid when query_type is 'correlation', "
                f"not '{self.query_type}'"
            )

        return self


# =============================================================================
# OUTPUT SCHEMAS
# =============================================================================


class MetricSeriesPoint(BaseModel):
    """A single data point in a time series."""

    timestamp: str = Field(description="ISO-8601 timestamp")
    value: float = Field(description="Metric value at this timestamp")


class MetricSummary(BaseModel):
    """Summary statistics for a metric time series."""

    min: float = Field(description="Minimum value in the series")
    max: float = Field(description="Maximum value in the series")
    avg: float = Field(description="Average (mean) value")
    p95: float = Field(description="95th percentile value")
    trend: TrendDirection = Field(description="Overall trend direction: up, down, or flat")
    data_points: int = Field(description="Number of data points in the series")


class AnomalyInfo(BaseModel):
    """Information about detected anomalies."""

    detected: bool = Field(description="Whether anomalies were detected")
    anomaly_count: int = Field(description="Number of anomalous data points")
    z_score_anomalies: int = Field(default=0, description="Anomalies detected by Z-score")
    moving_avg_anomalies: int = Field(
        default=0, description="Anomalies detected by moving average deviation"
    )
    threshold_breaches: int = Field(default=0, description="Number of threshold breaches")
    max_z_score: float | None = Field(default=None, description="Maximum absolute Z-score found")
    severity: str = Field(
        default="none", description="Anomaly severity: none, low, medium, high, critical"
    )


class MetricResult(BaseModel):
    """Complete result for a single metric: series data, summary statistics, anomaly findings."""

    metric: str = Field(description="Metric name")
    labels: dict[str, str] | None = Field(default=None, description="Label set for this series")
    series: list[MetricSeriesPoint] = Field(description="Time series data points")
    summary: MetricSummary = Field(description="Summary statistics")
    anomalies: AnomalyInfo | None = Field(default=None, description="Anomaly detection results")


class CorrelationResult(BaseModel):
    """Result of correlation analysis between two metrics."""

    metric_a: str = Field(description="First metric name (reference)")
    metric_b: str = Field(description="Second metric name")
    method: CorrelationMethod = Field(description="Correlation method used")
    coefficient: float = Field(description="Correlation coefficient (-1.0 to 1.0)", ge=-1.0, le=1.0)
    lag_seconds: int = Field(
        default=0, description="Time lag in seconds (0 for pearson, optimal lag for time_shifted)"
    )
    strength: CorrelationStrength = Field(description="Correlation strength interpretation")
    p_value: float | None = Field(
        default=None, description="Statistical significance (p-value), if computed"
    )
    interpretation: str = Field(description="Human-readable interpretation of the correlation")


class BaselineComparisonResult(BaseModel):
    """Result of baseline comparison analysis (current window vs. historical baseline)."""

    metric: str = Field(description="Metric name")
    current_avg: float = Field(description="Current window average")
    baseline_avg: float = Field(description="Historical baseline average")
    deviation_percent: float = Field(description="Percentage deviation from baseline")
    is_significant: bool = Field(description="Whether deviation is statistically significant")
    severity: str = Field(description="Deviation severity: normal, elevated, warning, critical")
    interpretation: str = Field(description="Human-readable interpretation")


class PrometheusAdvanceQueryOutput(BaseModel):
    """Complete output schema: results, summaries, anomalies, correlations, baseline comparisons."""

    status: str = Field(description="Query status: success or error")
    query_type: QueryType = Field(description="Type of query that was executed")
    time_range: dict[str, str] = Field(description="Executed time range")
    results: list[MetricResult] = Field(
        default_factory=list, description="Query results for each metric"
    )
    correlation_results: list[CorrelationResult] | None = Field(
        default=None, description="Correlation analysis results (only for correlation queries)"
    )
    baseline_comparisons: list[BaselineComparisonResult] | None = Field(
        default=None, description="Baseline comparison results (only for baseline_compare queries)"
    )
    anomalies_detected: bool = Field(
        default=False, description="Whether any anomalies were detected across all metrics"
    )
    overall_confidence: float = Field(
        default=1.0, description="Confidence score for the analysis (0.0 to 1.0)", ge=0.0, le=1.0
    )
    explanation: str = Field(description="Human-readable explanation of the query and findings")
    rca_insights: list[str] | None = Field(
        default=None, description="Root cause analysis insights derived from the data"
    )
    error: str | None = Field(default=None, description="Error message if status is 'error'")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Additional metadata (execution time, data points, etc.)"
    )
