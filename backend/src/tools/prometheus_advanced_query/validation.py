"""Input validation + guardrails for the advanced-query tool.

Ported from the reference ``validation.py``. The Pydantic ``args_schema`` already enforces
per-field shape (metric-name charset, step format, <=10 metrics); this layer enforces the
*cross-field* guardrails Pydantic can't express cheaply — time ordering, window/step/data-point
budgets, label safety, and correlation requirements — returning a structured ``(is_valid, error)``
so the engine can degrade to an error result instead of raising. Stateless and deterministic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from src.logging import get_logger

from .schemas import CorrelationInput, PrometheusAdvanceQueryInput, QueryType

_log = get_logger(__name__)

_STEP_RE = re.compile(r"^(\d+)([smh])$")
_LABEL_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_STEP_MULTIPLIERS = {"s": 1, "m": 60, "h": 3600}


class ValidationError(Exception):
    """Raised when a guardrail is violated. Carries structured context for reporting."""

    def __init__(
        self,
        message: str,
        field: str | None = None,
        value: Any = None,
        guardrail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.field = field
        self.value = value
        self.guardrail = guardrail


@dataclass
class GuardrailConfig:
    """Configurable guardrail limits (cardinality, time range, resolution, labels, lag)."""

    max_metrics_per_call: int = 10
    max_time_range_hours: int = 24
    min_step_seconds: int = 15
    max_data_points_per_series: int = 10000
    max_lag_seconds: int = 3600
    max_labels: int = 10
    max_label_value_length: int = 256

    # Regex blacklist: metric names carrying PromQL syntax (injection attempt). Redundant with the
    # args_schema charset check, kept as defense-in-depth for direct (non-tool) engine callers.
    blocked_patterns: list[str] = field(
        default_factory=lambda: [
            r".*\{.*\}.*",  # pre-formatted label selectors
            r".*\[.*\].*",  # range vectors
            r".*\(.*\).*",  # functions
        ]
    )


class InputValidator:
    """Validates metric names, time range, labels, and query-type requirements."""

    def __init__(self, config: GuardrailConfig | None = None) -> None:
        self.config = config or GuardrailConfig()
        self._compiled_blocked_patterns = [re.compile(p) for p in self.config.blocked_patterns]

    def validate(self, input_data: PrometheusAdvanceQueryInput) -> tuple[bool, str | None]:
        """Run all validations. Returns ``(True, None)`` or ``(False, error_message)``."""
        try:
            self.validate_metrics(input_data.metric_names)
            self.validate_time_range(input_data.time_range)
            self.validate_labels(input_data.labels)
            self.validate_query_type_requirements(
                input_data.query_type, input_data.metric_names, input_data.correlation
            )
            return True, None
        except ValidationError as exc:
            _log.warning("advanced_query_validation_failed", error=str(exc))
            return False, str(exc)

    def validate_metrics(self, metric_names: list[str]) -> None:
        """Enforce the metric-count budget and reject injection-prone names."""
        if len(metric_names) > self.config.max_metrics_per_call:
            raise ValidationError(
                f"Too many metrics: {len(metric_names)} exceeds limit of "
                f"{self.config.max_metrics_per_call}",
                field="metric_names",
                guardrail="max_metrics_per_call",
            )
        if len(metric_names) == 0:
            raise ValidationError(
                "At least one metric name is required",
                field="metric_names",
                guardrail="min_metrics",
            )
        for metric in metric_names:
            self._validate_single_metric(metric)

    def _validate_single_metric(self, metric: str) -> None:
        for pattern in self._compiled_blocked_patterns:
            if pattern.match(metric):
                raise ValidationError(
                    f"Invalid metric name '{metric}': contains blocked pattern "
                    "(possible injection attempt)",
                    field="metric_names",
                    value=metric,
                    guardrail="blocked_patterns",
                )

    def validate_time_range(self, time_range: Any) -> None:
        """Validate ISO-8601 timestamps, ordering, window budget, step, and data-point budget."""
        try:
            start_dt = datetime.fromisoformat(time_range.start.replace("Z", "+00:00"))
        except (ValueError, AttributeError) as exc:
            raise ValidationError(
                f"Invalid start timestamp: {time_range.start}. Expected ISO-8601 format",
                field="time_range.start",
                value=time_range.start,
                guardrail="timestamp_format",
            ) from exc

        try:
            end_dt = datetime.fromisoformat(time_range.end.replace("Z", "+00:00"))
        except (ValueError, AttributeError) as exc:
            raise ValidationError(
                f"Invalid end timestamp: {time_range.end}. Expected ISO-8601 format",
                field="time_range.end",
                value=time_range.end,
                guardrail="timestamp_format",
            ) from exc

        if start_dt >= end_dt:
            raise ValidationError(
                f"Invalid time range: start ({time_range.start}) must be before "
                f"end ({time_range.end})",
                field="time_range",
                guardrail="time_order",
            )

        duration = end_dt - start_dt
        max_duration = timedelta(hours=self.config.max_time_range_hours)
        if duration > max_duration:
            raise ValidationError(
                f"Time range too long: {duration} exceeds maximum of {max_duration}",
                field="time_range",
                guardrail="max_time_range_hours",
            )

        step_seconds = self._parse_step(time_range.step)
        if step_seconds < self.config.min_step_seconds:
            raise ValidationError(
                f"Step too small: {time_range.step} ({step_seconds}s) is below "
                f"minimum of {self.config.min_step_seconds}s",
                field="time_range.step",
                value=time_range.step,
                guardrail="min_step_seconds",
            )

        estimated_points = int(duration.total_seconds() / step_seconds)
        if estimated_points > self.config.max_data_points_per_series:
            raise ValidationError(
                f"Too many data points: estimated {estimated_points} points exceeds limit of "
                f"{self.config.max_data_points_per_series}. Increase step or reduce time range.",
                field="time_range",
                guardrail="max_data_points_per_series",
            )

    def _parse_step(self, step: str) -> int:
        match = _STEP_RE.match(step)
        if not match:
            raise ValidationError(
                f"Invalid step format: {step}. Expected format: <number><s|m|h>",
                field="time_range.step",
                value=step,
                guardrail="step_format",
            )
        return int(match.group(1)) * _STEP_MULTIPLIERS[match.group(2)]

    def validate_labels(self, labels: dict[str, str] | None) -> None:
        """Enforce label count, name charset, value length, and injection-char rejection."""
        if labels is None:
            return

        if len(labels) > self.config.max_labels:
            raise ValidationError(
                f"Too many labels: {len(labels)} exceeds limit of {self.config.max_labels}",
                field="labels",
                guardrail="max_labels",
            )

        for key, value in labels.items():
            if not _LABEL_NAME_RE.match(key):
                raise ValidationError(
                    f"Invalid label name: '{key}'. Label names must start with a letter or "
                    "underscore and contain only alphanumeric characters and underscores.",
                    field=f"labels.{key}",
                    value=key,
                    guardrail="label_name_format",
                )

            if len(value) > self.config.max_label_value_length:
                raise ValidationError(
                    f"Label value too long: '{key}' value has {len(value)} chars, "
                    f"max is {self.config.max_label_value_length}",
                    field=f"labels.{key}",
                    guardrail="max_label_value_length",
                )

            if re.search(r"[{}()\[\]]", value) or re.search(r"[\n\r]", value):
                raise ValidationError(
                    f"Invalid characters in label value for '{key}': "
                    "contains potential injection characters",
                    field=f"labels.{key}",
                    guardrail="label_value_safety",
                )

    def validate_query_type_requirements(
        self,
        query_type: QueryType,
        metric_names: list[str],
        correlation: CorrelationInput | None,
    ) -> None:
        """Correlation queries need >=2 metrics and a reference metric present in the list."""
        if query_type != QueryType.CORRELATION:
            return

        if correlation is None:
            raise ValidationError(
                "Correlation parameters required when query_type is 'correlation'",
                field="correlation",
                guardrail="correlation_required",
            )

        if len(metric_names) < 2:
            raise ValidationError(
                "At least 2 metrics required for correlation analysis",
                field="metric_names",
                guardrail="correlation_min_metrics",
            )

        if correlation.reference_metric not in metric_names:
            raise ValidationError(
                f"Reference metric '{correlation.reference_metric}' must be in the "
                f"metric_names list: {metric_names}",
                field="correlation.reference_metric",
                value=correlation.reference_metric,
                guardrail="reference_metric_exists",
            )

        if correlation.max_lag_seconds > self.config.max_lag_seconds:
            raise ValidationError(
                f"Max lag too large: {correlation.max_lag_seconds}s exceeds limit of "
                f"{self.config.max_lag_seconds}s",
                field="correlation.max_lag_seconds",
                value=correlation.max_lag_seconds,
                guardrail="max_lag_seconds",
            )
