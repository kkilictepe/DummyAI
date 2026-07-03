"""Transport-client ERROR paths must emit structured log events.

Wraps the error-triggering client calls in ``structlog.testing.capture_logs`` and asserts the
exact event names the instrumented source emits. Hermetic: Prometheus via ``respx``; the ES
underlying client is stubbed to raise.
"""

from __future__ import annotations

import httpx
import pytest
import respx
import structlog

from src.clients.elasticsearch import ElasticsearchClient
from src.clients.prometheus import PrometheusClient

BASE = "http://prom.test:9090"


def _prom() -> PrometheusClient:
    return PrometheusClient(BASE)


def _events(caplog: list[dict[str, object]]) -> list[object]:
    return [record["event"] for record in caplog]


@respx.mock
async def test_prometheus_transport_failure_logs_event() -> None:
    respx.get(f"{BASE}/api/v1/query").mock(side_effect=httpx.ConnectError("refused"))
    client = _prom()
    with structlog.testing.capture_logs() as caplog:
        resp = await client.instant_query("up")
    await client.aclose()

    assert not resp.success
    assert "prometheus_query_failed" in _events(caplog)


@respx.mock
async def test_prometheus_promql_error_logs_event() -> None:
    respx.get(f"{BASE}/api/v1/query").mock(
        return_value=httpx.Response(
            400,
            json={"status": "error", "errorType": "bad_data", "error": "parse error: unexpected"},
        )
    )
    client = _prom()
    with structlog.testing.capture_logs() as caplog:
        resp = await client.instant_query("this is not promql")
    await client.aclose()

    assert not resp.success
    assert "prometheus_query_returned_error" in _events(caplog)


@respx.mock
async def test_prometheus_label_values_http_error_logs_event() -> None:
    respx.get(f"{BASE}/api/v1/label/__name__/values").mock(
        side_effect=httpx.ConnectError("refused")
    )
    client = _prom()
    with structlog.testing.capture_logs() as caplog:
        values = await client.label_values()
    await client.aclose()

    assert values == []
    assert "prometheus_label_values_failed" in _events(caplog)


async def test_elasticsearch_request_failure_logs_event_and_reraises() -> None:
    client = ElasticsearchClient("http://es.test:9200", default_index="sap-logs-*")

    class _RaisingES:
        async def search(
            self, index: str, body: dict[str, object], **kw: object
        ) -> dict[str, object]:
            raise RuntimeError("es down")

    client._client = _RaisingES()  # type: ignore[assignment]

    with structlog.testing.capture_logs() as caplog:
        with pytest.raises(RuntimeError):
            await client.search_logs({"query": {"match_all": {}}})

    assert "elasticsearch_request_failed" in _events(caplog)
