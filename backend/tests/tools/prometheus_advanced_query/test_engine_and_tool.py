"""QueryEngine + the ``prometheus_metrics_advance_query`` tool, end to end.

The Prometheus boundary is a :class:`FakeProm` registered via ``set_clients`` (so the tool fetches
it through ``get_prometheus_client``). The genuine ``parse_metric_data`` runs; only the network
methods are canned.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Any

import pytest

from src.clients import close_clients, set_clients
from src.clients.prometheus import PrometheusResponse
from src.tools.prometheus_advanced_query.engine import AdvancedQueryConfig, QueryEngine
from src.tools.prometheus_advanced_query.schemas import (
    CorrelationInput,
    CorrelationMethod,
    PrometheusAdvanceQueryInput,
    QueryType,
    TimeRangeInput,
)
from src.tools.prometheus_advanced_query.tool import prometheus_metrics_advance_query as adv_tool

from ._helpers import FakeES, FakeProm, matrix, matrix_multi, ramp, vector

_WINDOW = {"start": "2024-01-20T10:00:00Z", "end": "2024-01-20T11:00:00Z", "step": "60s"}


@pytest.fixture
async def register() -> AsyncIterator[Any]:
    """Register a FakeProm for the tool and tear the clients down afterwards."""

    def _register(
        range_maps: list[dict[str, PrometheusResponse]],
        instant_map: dict[str, PrometheusResponse] | None = None,
    ) -> FakeProm:
        fake = FakeProm(range_maps, instant_map)
        set_clients(fake, FakeES())  # type: ignore[arg-type]
        return fake

    yield _register
    await close_clients()


# ---------------------------------------------------------------------------
# Tool end-to-end
# ---------------------------------------------------------------------------


async def test_range_query_success_and_enum_serialized_as_string(register: Any) -> None:
    register([{"sap_cpu": matrix("sap_cpu", ramp([10, 20, 30, 40, 50]))}])
    result = await adv_tool.ainvoke(
        {"metric_names": ["sap_cpu"], "time_range": _WINDOW, "query_type": "range"}
    )
    assert result["status"] == "success"
    assert result["query_type"] == "range"  # StrEnum -> plain string via model_dump(mode="json")
    assert len(result["results"]) == 1
    assert result["results"][0]["summary"]["avg"] == 30.0


async def test_system_id_folded_into_label(register: Any) -> None:
    fake = register([{"sap_cpu": matrix("sap_cpu", ramp([1, 2, 3]))}])
    await adv_tool.ainvoke({"metric_names": ["sap_cpu"], "time_range": _WINDOW, "system_id": "KHP"})
    emitted = dict(fake.range_calls[0][0])
    assert emitted["sap_cpu"] == 'sap_cpu{system_id="KHP"}'


async def test_explicit_system_id_overrides_labels(register: Any) -> None:
    fake = register([{"sap_cpu": matrix("sap_cpu", ramp([1, 2, 3]))}])
    await adv_tool.ainvoke(
        {
            "metric_names": ["sap_cpu"],
            "time_range": _WINDOW,
            "system_id": "KHP",
            "labels": {"system_id": "KBP"},
        }
    )
    emitted = dict(fake.range_calls[0][0])
    assert 'system_id="KHP"' in emitted["sap_cpu"]
    assert "KBP" not in emitted["sap_cpu"]


async def test_anomaly_check_detects_spike(register: Any) -> None:
    register([{"m": matrix("m", ramp([10.0] * 14 + [500.0]))}])
    result = await adv_tool.ainvoke(
        {"metric_names": ["m"], "time_range": _WINDOW, "query_type": "anomaly_check"}
    )
    assert result["status"] == "success"
    assert result["query_type"] == "anomaly_check"
    assert result["anomalies_detected"] is True
    assert result["metadata"]["anomaly_summary"]["total_anomalies"] >= 1
    assert result["results"][0]["anomalies"]["detected"] is True


async def test_baseline_compare_flags_significant_deviation(register: Any) -> None:
    # First query_multiple call -> current window (high); second -> baseline window (low).
    register(
        [
            {"m": matrix("m", ramp([200.0] * 6))},
            {"m": matrix("m", ramp([100.0] * 6))},
        ]
    )
    result = await adv_tool.ainvoke(
        {"metric_names": ["m"], "time_range": _WINDOW, "query_type": "baseline_compare"}
    )
    assert result["query_type"] == "baseline_compare"
    comparisons = result["baseline_comparisons"]
    assert len(comparisons) == 1
    assert comparisons[0]["deviation_percent"] == 100.0
    assert comparisons[0]["is_significant"] is True
    assert result["anomalies_detected"] is True


async def test_baseline_compare_queries_two_offset_windows(register: Any) -> None:
    fake = register([{"m": matrix("m", ramp([200.0] * 6))}, {"m": matrix("m", ramp([100.0] * 6))}])
    await adv_tool.ainvoke(
        {"metric_names": ["m"], "time_range": _WINDOW, "query_type": "baseline_compare"}
    )
    assert len(fake.range_calls) == 2
    cur_start = datetime.fromisoformat(fake.range_calls[0][1].replace("Z", "+00:00"))
    cur_end = datetime.fromisoformat(fake.range_calls[0][2].replace("Z", "+00:00"))
    base_start = datetime.fromisoformat(fake.range_calls[1][1].replace("Z", "+00:00"))
    base_end = datetime.fromisoformat(fake.range_calls[1][2].replace("Z", "+00:00"))
    # Baseline window is EXACTLY 24h earlier with the window duration preserved.
    assert base_start == cur_start - timedelta(hours=24)
    assert base_end == cur_end - timedelta(hours=24)


async def test_correlation_reports_strong_link(register: Any) -> None:
    rising = ramp([float(i) for i in range(12)])
    register([{"sap_a": matrix("sap_a", rising), "sap_b": matrix("sap_b", rising)}])
    result = await adv_tool.ainvoke(
        {
            "metric_names": ["sap_a", "sap_b"],
            "time_range": _WINDOW,
            "query_type": "correlation",
            "correlation": {
                "method": "pearson",
                "reference_metric": "sap_a",
                "max_lag_seconds": 300,
            },
        }
    )
    assert result["query_type"] == "correlation"
    correlations = result["correlation_results"]
    assert len(correlations) == 1
    assert correlations[0]["metric_b"] == "sap_b"
    assert correlations[0]["coefficient"] == 1.0
    assert correlations[0]["strength"] == "strong"


async def test_instant_query_uses_instant_endpoint(register: Any) -> None:
    fake = register([], instant_map={"sap_cpu": vector("sap_cpu", 1000, "42")})
    result = await adv_tool.ainvoke(
        {"metric_names": ["sap_cpu"], "time_range": _WINDOW, "query_type": "instant"}
    )
    assert result["query_type"] == "instant"
    assert len(fake.instant_calls) == 1
    assert result["results"][0]["summary"]["avg"] == 42.0


async def test_validation_error_returns_error_result(register: Any) -> None:
    register([{"m": matrix("m", ramp([1, 2, 3]))}])
    # start after end trips the InputValidator (not the schema) -> structured error, not a raise.
    result = await adv_tool.ainvoke(
        {
            "metric_names": ["m"],
            "time_range": {
                "start": "2024-01-20T11:00:00Z",
                "end": "2024-01-20T10:00:00Z",
                "step": "60s",
            },
        }
    )
    assert result["status"] == "error"
    assert result["error"] is not None and "must be before" in result["error"]


# ---------------------------------------------------------------------------
# Engine unit-level
# ---------------------------------------------------------------------------


async def test_engine_skips_failed_metric_but_still_succeeds() -> None:
    engine = QueryEngine(AdvancedQueryConfig())
    fake = FakeProm([{"m": PrometheusResponse(success=False, error_message="boom")}])
    input_data = PrometheusAdvanceQueryInput(
        metric_names=["m"],
        time_range=TimeRangeInput(**_WINDOW),
        query_type=QueryType.RANGE,
    )
    out = await engine.run(input_data, fake)  # type: ignore[arg-type]
    assert out.status == "success"
    assert out.results == []


async def test_engine_config_from_tools_yaml_matches_committed_values() -> None:
    cfg = AdvancedQueryConfig.from_tools_config()
    assert cfg.max_metrics_per_call == 10
    assert cfg.max_time_range_hours == 24
    assert cfg.z_score_threshold == 3.0
    assert cfg.default_baseline_offset_hours == 24


def _input(
    query_type: QueryType, metric_names: list[str], **extra: Any
) -> PrometheusAdvanceQueryInput:
    return PrometheusAdvanceQueryInput(
        metric_names=metric_names,
        time_range=TimeRangeInput(**_WINDOW),
        query_type=query_type,
        **extra,
    )


async def test_range_merges_multiple_app_server_series_through_engine() -> None:
    # The multi-app-server merge branch (len(series) > 1) routed through the engine, not just the
    # normalizer in isolation. NaN-aware, order-independent, one merged result.
    fake = FakeProm(
        [
            {
                "m": matrix_multi(
                    "m",
                    [
                        ({"monitoring_context": "app01"}, ramp([10, 20, 30])),
                        ({"monitoring_context": "app02"}, ramp([30, 40, 50])),
                    ],
                )
            }
        ]
    )
    out = await QueryEngine(AdvancedQueryConfig()).run(_input(QueryType.RANGE, ["m"]), fake)  # type: ignore[arg-type]
    assert len(out.results) == 1
    merged = out.results[0]
    assert merged.labels == {"_merged": "true", "_series_count": "2"}
    assert [p.value for p in merged.series] == [20.0, 30.0, 40.0]


async def test_anomaly_flag_off_suppresses_per_metric_anomalies_in_all_strategies() -> None:
    engine = QueryEngine(AdvancedQueryConfig(enable_anomaly_detection=False))
    spiky = matrix("m", ramp([10.0] * 14 + [500.0]))

    anomaly = await engine.run(_input(QueryType.ANOMALY_CHECK, ["m"]), FakeProm([{"m": spiky}]))  # type: ignore[arg-type]
    assert anomaly.results[0].anomalies is None
    assert anomaly.anomalies_detected is False

    baseline_fake = FakeProm(
        [{"m": matrix("m", ramp([200.0] * 6))}, {"m": matrix("m", ramp([100.0] * 6))}]
    )
    baseline = await engine.run(_input(QueryType.BASELINE_COMPARE, ["m"]), baseline_fake)  # type: ignore[arg-type]
    assert baseline.results[0].anomalies is None  # per-metric z-score suppressed ...
    assert baseline.baseline_comparisons is not None
    assert baseline.baseline_comparisons[0].is_significant is True  # ... but baseline math runs

    rising = ramp([float(i) for i in range(12)])
    corr_fake = FakeProm([{"a": matrix("a", rising), "b": matrix("b", rising)}])
    corr = await engine.run(
        _input(
            QueryType.CORRELATION,
            ["a", "b"],
            correlation=CorrelationInput(
                method=CorrelationMethod.PEARSON, reference_metric="a", max_lag_seconds=300
            ),
        ),
        corr_fake,  # type: ignore[arg-type]
    )
    assert corr.correlation_results is not None
    assert all(r.anomalies is None for r in corr.results)


class _RaisingProm(FakeProm):
    """A client whose network call raises with a secret-looking message."""

    async def query_multiple(self, *args: Any, **kwargs: Any) -> dict[str, PrometheusResponse]:
        raise RuntimeError("failed to connect to http://secret-internal-prom:9090")


async def test_unexpected_exception_masked_as_generic_error_without_leak() -> None:
    # engine.run must swallow the raw exception (which can carry the Prometheus URL) and return a
    # generic message; the result dict is relayed to the browser via the AG-UI tool-result event.
    engine = QueryEngine(AdvancedQueryConfig())
    out = await engine.run(_input(QueryType.RANGE, ["m"]), _RaisingProm([{}]))  # type: ignore[arg-type]
    assert out.status == "error"
    assert out.error == "Internal error while executing the advanced query."
    assert "secret-internal-prom" not in json.dumps(out.model_dump(mode="json"))


async def test_label_quote_value_is_escaped_end_to_end(register: Any) -> None:
    # Validation deliberately permits a bare double-quote in a label value; the builder MUST escape
    # it so it cannot break out of the quoted PromQL string. Asserted through the full tool path.
    fake = register([{"sap_cpu": matrix("sap_cpu", ramp([1, 2, 3]))}])
    await adv_tool.ainvoke(
        {"metric_names": ["sap_cpu"], "time_range": _WINDOW, "labels": {"app": 'KHP"'}}
    )
    emitted = dict(fake.range_calls[0][0])
    assert emitted["sap_cpu"] == 'sap_cpu{app="KHP\\""}'
