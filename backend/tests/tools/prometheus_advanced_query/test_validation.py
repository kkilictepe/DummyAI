"""InputValidator: cross-field guardrails the args_schema can't express."""

from __future__ import annotations

import pytest

from src.tools.prometheus_advanced_query.schemas import (
    CorrelationInput,
    CorrelationMethod,
    PrometheusAdvanceQueryInput,
    QueryType,
    TimeRangeInput,
)
from src.tools.prometheus_advanced_query.validation import (
    GuardrailConfig,
    InputValidator,
    ValidationError,
)

_VALIDATOR = InputValidator()


def _input(
    *,
    metric_names: list[str] | None = None,
    start: str = "2024-01-20T10:00:00Z",
    end: str = "2024-01-20T11:00:00Z",
    step: str = "60s",
    query_type: QueryType = QueryType.RANGE,
    labels: dict[str, str] | None = None,
    correlation: CorrelationInput | None = None,
) -> PrometheusAdvanceQueryInput:
    return PrometheusAdvanceQueryInput(
        metric_names=metric_names or ["sap_cpu"],
        time_range=TimeRangeInput(start=start, end=end, step=step),
        query_type=query_type,
        labels=labels,
        correlation=correlation,
    )


def test_valid_input_passes() -> None:
    ok, err = _VALIDATOR.validate(_input())
    assert ok is True
    assert err is None


@pytest.mark.parametrize(
    "start,end",
    [
        ("2024-01-20T10:00:00z", "2024-01-20T11:00:00z"),  # lowercase 'z' designator
        (" 2024-01-20T10:00:00Z ", " 2024-01-20T11:00:00Z "),  # surrounding whitespace
    ],
)
def test_iso_timestamps_tolerate_lowercase_z_and_whitespace(start: str, end: str) -> None:
    # A valid UTC window phrased with lowercase 'z' or padding must pass, not be rejected as an
    # invalid timestamp format.
    ok, err = _VALIDATOR.validate(_input(start=start, end=end))
    assert ok is True, err
    assert err is None


def test_start_not_before_end_is_rejected() -> None:
    ok, err = _VALIDATOR.validate(_input(start="2024-01-20T11:00:00Z", end="2024-01-20T10:00:00Z"))
    assert ok is False
    assert err is not None and "must be before" in err


def test_step_below_minimum_is_rejected() -> None:
    # '5s' is a valid step *format* (passes the schema) but below the 15s guardrail floor.
    ok, err = _VALIDATOR.validate(_input(step="5s"))
    assert ok is False
    assert err is not None and "Step too small" in err


def test_window_longer_than_max_is_rejected() -> None:
    ok, err = _VALIDATOR.validate(
        _input(start="2024-01-20T00:00:00Z", end="2024-01-22T00:00:00Z")  # 48h > 24h
    )
    assert ok is False
    assert err is not None and "Time range too long" in err


def test_too_many_data_points_rejected_with_tight_budget() -> None:
    # Unreachable under default limits (min_step 15s vs max 24h caps points), so prove the
    # guardrail with a tight custom budget: a 1h/60s window yields 60 points > 10.
    validator = InputValidator(GuardrailConfig(max_data_points_per_series=10))
    ok, err = validator.validate(_input())
    assert ok is False
    assert err is not None and "Too many data points" in err


def test_too_many_metrics_rejected_by_validate_metrics() -> None:
    # >10 is blocked by the schema at construction; the validator method enforces it directly too.
    with pytest.raises(ValidationError, match="Too many metrics"):
        _VALIDATOR.validate_metrics([f"m{i}" for i in range(11)])


def test_blocked_injection_metric_rejected() -> None:
    with pytest.raises(ValidationError, match="blocked pattern"):
        _VALIDATOR.validate_metrics(["sap_cpu{system_id='x'}"])


def test_correlation_reference_must_be_in_metric_names() -> None:
    ok, err = _VALIDATOR.validate(
        _input(
            metric_names=["a", "b"],
            query_type=QueryType.CORRELATION,
            correlation=CorrelationInput(
                method=CorrelationMethod.PEARSON, reference_metric="c", max_lag_seconds=300
            ),
        )
    )
    assert ok is False
    assert err is not None and "must be in the metric_names list" in err


def test_correlation_requires_at_least_two_metrics() -> None:
    ok, err = _VALIDATOR.validate(
        _input(
            metric_names=["a"],
            query_type=QueryType.CORRELATION,
            correlation=CorrelationInput(
                method=CorrelationMethod.PEARSON, reference_metric="a", max_lag_seconds=300
            ),
        )
    )
    assert ok is False
    assert err is not None and "At least 2 metrics" in err


def test_label_injection_char_rejected() -> None:
    ok, err = _VALIDATOR.validate(_input(labels={"system_id": "KHP{"}))
    assert ok is False
    assert err is not None and "injection characters" in err


def test_invalid_label_name_rejected() -> None:
    ok, err = _VALIDATOR.validate(_input(labels={"bad-name": "x"}))
    assert ok is False
    assert err is not None and "Invalid label name" in err
