"""tool_sap_metric_categorized: PromQL construction, summarization, anomalies, discovery.

The Prometheus boundary is stubbed by subclassing :class:`PrometheusClient` and overriding only
``query_multiple`` — the genuine ``parse_metric_data`` runs, so parsing is exercised too.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from src.clients import close_clients, set_clients
from src.clients.prometheus import PrometheusClient, PrometheusResponse
from src.tools.sap_metric_categorized import tool_sap_metric_categorized as sap_tool


class _FakeES:
    async def close(self) -> None:
        pass


class _FakeProm(PrometheusClient):
    """Real client (for parse_metric_data) with a canned ``query_multiple``."""

    def __init__(self, responses: dict[str, PrometheusResponse]) -> None:
        super().__init__("http://prom.test:9090")
        self._responses = responses
        self.queries: list[tuple[str, str]] = []
        self.start: str | None = None
        self.end: str | None = None
        self.step: str | None = None

    async def query_multiple(
        self,
        queries: list[tuple[str, str]],
        start: str,
        end: str,
        step: str,
    ) -> dict[str, PrometheusResponse]:
        self.queries = list(queries)
        self.start, self.end, self.step = start, end, step
        return {name: self._responses[name] for name, _ in queries if name in self._responses}


def _matrix(
    prometheus_name: str,
    system_id: str,
    values: list[list[Any]],
    monitoring_context: str | None = None,
) -> PrometheusResponse:
    metric: dict[str, str] = {"__name__": prometheus_name, "system_id": system_id}
    if monitoring_context:
        metric["monitoring_context"] = monitoring_context
    return PrometheusResponse(
        success=True,
        data={"resultType": "matrix", "result": [{"metric": metric, "values": values}]},
    )


@pytest.fixture
async def register() -> AsyncIterator[Any]:
    """Register a fake Prometheus client for the tool to fetch via get_prometheus_client()."""
    created: dict[str, _FakeProm] = {}

    def _register(responses: dict[str, PrometheusResponse]) -> _FakeProm:
        fake = _FakeProm(responses)
        set_clients(fake, _FakeES())  # type: ignore[arg-type]
        created["fake"] = fake
        return fake

    yield _register
    await close_clients()


_RAMP = [[1000, "70"], [1060, "72"], [1120, "80"], [1180, "85"], [1240, "88"], [1300, "90"]]


async def test_builds_per_key_promql_with_system_id(register: Any) -> None:
    fake = register(
        {
            "sap_application_cpu_utilisation": _matrix(
                "sap_application_cpu_utilisation_percent", "KHP", _RAMP, "KHP_APP01"
            )
        }
    )
    result = await sap_tool.ainvoke(
        {"system_id": "KHP", "category": "cpu_overview", "time_range": "1h"}
    )

    assert result["status"] == "success"
    # every emitted query is system-scoped PromQL
    queries = dict(fake.queries)
    assert (
        queries["sap_application_cpu_utilisation"]
        == 'sap_application_cpu_utilisation_percent{system_id="KHP"}'
    )
    assert all(q.endswith('{system_id="KHP"}') for q in queries.values())


async def test_monitoring_context_added_to_selector_and_suppresses_discovery(
    register: Any,
) -> None:
    fake = register(
        {
            "sap_application_cpu_utilisation": _matrix(
                "sap_application_cpu_utilisation_percent", "KHP", _RAMP, "KHP_APP01"
            )
        }
    )
    result = await sap_tool.ainvoke(
        {
            "system_id": "KHP",
            "category": "cpu_overview",
            "time_range": "1h",
            "monitoring_context": "KHP_APP01",
        }
    )

    assert dict(fake.queries)["sap_application_cpu_utilisation"] == (
        'sap_application_cpu_utilisation_percent{system_id="KHP", monitoring_context="KHP_APP01"}'
    )
    # discovery is only offered when no context was pinned
    assert "available_application_servers" not in result


async def test_summary_has_pure_python_percentiles(register: Any) -> None:
    register(
        {
            "sap_application_cpu_utilisation": _matrix(
                "sap_application_cpu_utilisation_percent", "KHP", _RAMP
            )
        }
    )
    result = await sap_tool.ainvoke(
        {"system_id": "KHP", "category": "cpu_overview", "time_range": "1h"}
    )
    summary = result["summaries"]["sap_application_cpu_utilisation"]
    assert summary["status"] == "ok"
    assert summary["min"] == 70.0
    assert summary["max"] == 90.0
    assert summary["current"] == 90.0
    assert summary["p50"] == 82.5  # linear interpolation over the 6-point ramp


async def test_anomaly_flagged_when_current_exceeds_threshold(register: Any) -> None:
    register(
        {
            "sap_application_cpu_utilisation": _matrix(
                "sap_application_cpu_utilisation_percent", "KHP", _RAMP
            )
        }
    )
    result = await sap_tool.ainvoke(
        {"system_id": "KHP", "category": "cpu_overview", "time_range": "1h"}
    )
    anomalies = {a["metric"]: a for a in result["anomalies"]}
    cpu = anomalies["sap_application_cpu_utilisation"]
    assert cpu["severity"] == "warning"  # 90 > 75 but < 112.5
    assert cpu["threshold"] == 75.0
    assert cpu["current_value"] == 90.0


async def test_critical_severity_when_far_over_threshold(register: Any) -> None:
    hot = [[1000, "150"], [1060, "160"], [1120, "170"], [1180, "175"], [1240, "180"], [1300, "200"]]
    register(
        {
            "sap_application_cpu_utilisation": _matrix(
                "sap_application_cpu_utilisation_percent", "KHP", hot
            )
        }
    )
    result = await sap_tool.ainvoke(
        {"system_id": "KHP", "category": "cpu_overview", "time_range": "1h"}
    )
    cpu = {a["metric"]: a for a in result["anomalies"]}["sap_application_cpu_utilisation"]
    assert cpu["severity"] == "critical"  # 200 > 75 * 1.5


async def test_composite_category_expands_to_all_its_keys(register: Any) -> None:
    fake = register({})  # no data — we only assert which metrics were queried
    result = await sap_tool.ainvoke(
        {"system_id": "KHP", "category": "cpu_overview", "time_range": "5m"}
    )
    queried_keys = {name for name, _ in fake.queries}
    assert queried_keys == {
        "sap_application_cpu_utilisation",
        "hana_cpu_utilisation",
        "sap_application_dialog_wp_utilisation",
        "sap_application_background_wp_utilisation",
        "sap_application_dialog_steps",
        "sap_application_users_logged_in",
    }
    assert result["context"]["query_metadata"]["metrics_queried"] == 6


async def test_discovery_lists_servers_from_returned_labels(register: Any) -> None:
    register(
        {
            "sap_application_cpu_utilisation": _matrix(
                "sap_application_cpu_utilisation_percent", "KHP", _RAMP, "KHP_APP01"
            ),
            "hana_cpu_utilisation": _matrix(
                "hana_cpu_utilization_percent", "KHP", _RAMP, "KHP_APP02"
            ),
        }
    )
    result = await sap_tool.ainvoke(
        {"system_id": "KHP", "category": "cpu_overview", "time_range": "1h"}
    )
    assert result["available_application_servers"] == ["KHP_APP01", "KHP_APP02"]


async def test_dropped_and_failed_queries_are_reported(register: Any) -> None:
    register(
        {
            # cpu present + ok
            "sap_application_cpu_utilisation": _matrix(
                "sap_application_cpu_utilisation_percent", "KHP", _RAMP
            ),
            # hana present but a Prometheus error
            "hana_cpu_utilisation": PrometheusResponse(success=False, error_message="boom"),
            # all other keys omitted -> dropped by query_multiple
        }
    )
    result = await sap_tool.ainvoke(
        {"system_id": "KHP", "category": "cpu_overview", "time_range": "1h"}
    )
    summaries = result["summaries"]
    assert summaries["sap_application_cpu_utilisation"]["status"] == "ok"
    # failed path preserves the upstream error message ...
    assert summaries["hana_cpu_utilisation"]["status"] == "error"
    assert summaries["hana_cpu_utilisation"]["error"] == "boom"
    # ... while a key omitted from query_multiple's result carries the distinct "query dropped"
    # text (the dropped vs failed distinction must not silently collapse).
    assert summaries["sap_application_dialog_steps"]["status"] == "error"
    assert summaries["sap_application_dialog_steps"]["error"] == "query dropped"


async def test_no_data_when_series_is_empty(register: Any) -> None:
    # A success response that parses to zero points -> summarizer no_data + the no_data bucket.
    register(
        {
            "sap_application_cpu_utilisation": PrometheusResponse(
                success=True, data={"resultType": "matrix", "result": []}
            )
        }
    )
    result = await sap_tool.ainvoke(
        {"system_id": "KHP", "category": "cpu_overview", "time_range": "1h"}
    )
    assert result["summaries"]["sap_application_cpu_utilisation"]["status"] == "no_data"
    no_data_metrics = [
        issue["metric"] for issue in result["context"]["metrics_with_issues"]["no_data"]
    ]
    assert "sap_application_cpu_utilisation" in no_data_metrics
    assert result["context"]["query_metadata"]["metrics_no_data"] >= 1


async def test_unknown_category_returns_error(register: Any) -> None:
    register({})
    # bypass the Literal arg schema to exercise the defensive guard against config drift
    result = await sap_tool.coroutine(system_id="KHP", category="does_not_exist")  # type: ignore[arg-type]
    assert result["status"] == "error"
    assert "Unknown category" in result["error"]


async def test_bad_time_range_returns_error(register: Any) -> None:
    register({})
    result = await sap_tool.ainvoke(
        {"system_id": "KHP", "category": "cpu_overview", "time_range": "banana"}
    )
    assert result["status"] == "error"
    assert "Invalid time window" in result["error"]


async def test_non_finite_end_returns_error_not_crash(register: Any) -> None:
    register({})
    # 'inf' float-parses but int(inf) overflows; the tool must return an error dict, never raise.
    result = await sap_tool.ainvoke(
        {"system_id": "KHP", "category": "cpu_overview", "time_range": "5m", "end": "inf"}
    )
    assert result["status"] == "error"
    assert "finite" in result["error"]


async def test_label_values_are_promql_escaped(register: Any) -> None:
    fake = register({})
    await sap_tool.coroutine(  # bypass schema to feed an adversarial system_id
        system_id='KHP"} evil{',
        category="cpu_overview",
        time_range="5m",
    )
    # the injected quote is backslash-escaped so it cannot break out of the label string
    # (the '{' that remains is now harmlessly *inside* the quoted value).
    assert (
        fake.queries[0][1] == 'sap_application_cpu_utilisation_percent{system_id="KHP\\"} evil{"}'
    )
