"""Args-schema (Pydantic) validators: shape enforced before the tool body runs."""

from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError

from src.tools.prometheus_advanced_query.schemas import (
    CorrelationInput,
    CorrelationMethod,
    PrometheusAdvanceQueryInput,
    QueryType,
    TimeRangeInput,
)

_TR = TimeRangeInput(start="2024-01-20T10:00:00Z", end="2024-01-20T11:00:00Z", step="60s")


def test_metric_names_deduplicated_order_preserving() -> None:
    model = PrometheusAdvanceQueryInput(metric_names=["a", "b", "a", "c"], time_range=_TR)
    assert model.metric_names == ["a", "b", "c"]


def test_more_than_ten_metrics_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        PrometheusAdvanceQueryInput(metric_names=[f"m{i}" for i in range(11)], time_range=_TR)


def test_injection_char_in_metric_name_rejected() -> None:
    with pytest.raises(PydanticValidationError, match="Invalid metric name"):
        PrometheusAdvanceQueryInput(metric_names=["sap_cpu{x}"], time_range=_TR)


def test_bad_step_format_rejected() -> None:
    with pytest.raises(PydanticValidationError, match="Invalid step format"):
        TimeRangeInput(start="2024-01-20T10:00:00Z", end="2024-01-20T11:00:00Z", step="5min")


def test_correlation_required_when_query_type_is_correlation() -> None:
    with pytest.raises(PydanticValidationError, match="correlation parameter is required"):
        PrometheusAdvanceQueryInput(
            metric_names=["a", "b"], time_range=_TR, query_type=QueryType.CORRELATION
        )


def test_correlation_rejected_for_non_correlation_query() -> None:
    with pytest.raises(PydanticValidationError, match="only valid when query_type"):
        PrometheusAdvanceQueryInput(
            metric_names=["a", "b"],
            time_range=_TR,
            query_type=QueryType.RANGE,
            correlation=CorrelationInput(
                method=CorrelationMethod.PEARSON, reference_metric="a", max_lag_seconds=300
            ),
        )


def test_max_lag_seconds_bounded() -> None:
    with pytest.raises(PydanticValidationError):
        CorrelationInput(
            method=CorrelationMethod.PEARSON, reference_metric="a", max_lag_seconds=99999
        )
