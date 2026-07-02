"""PrometheusClient: parsing, GET/POST switch, error mapping, label discovery, fan-out."""

from __future__ import annotations

import math
from datetime import UTC, datetime

import httpx
import pytest
import respx

from src.clients.prometheus import PrometheusClient, PrometheusResponse, build_prometheus_client
from src.config import Settings

BASE = "http://prom.test:9090"

_EMPTY_VECTOR = {"status": "success", "data": {"resultType": "vector", "result": []}}


def _client() -> PrometheusClient:
    return PrometheusClient(BASE)


@respx.mock
async def test_instant_query_parses_vector() -> None:
    respx.get(f"{BASE}/api/v1/query").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "resultType": "vector",
                    "result": [
                        {
                            "metric": {"__name__": "up", "system_id": "KHP"},
                            "value": [1719878400, "1"],
                        }
                    ],
                },
            },
        )
    )
    client = _client()
    resp = await client.instant_query("up")
    await client.aclose()

    assert resp.success
    series = client.parse_metric_data(resp)
    assert len(series) == 1
    assert series[0].metric_name == "up"
    assert series[0].labels == {"system_id": "KHP"}
    assert series[0].values == [(1719878400.0, 1.0)]


@respx.mock
async def test_range_query_parses_matrix_and_nan() -> None:
    respx.get(f"{BASE}/api/v1/query_range").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "resultType": "matrix",
                    "result": [
                        {
                            "metric": {"__name__": "cpu", "system_id": "KHP"},
                            "values": [[1719878400, "0.5"], [1719878460, "NaN"]],
                        }
                    ],
                },
            },
        )
    )
    client = _client()
    resp = await client.range_query("cpu", "1719878400", "1719878460", "60s")
    await client.aclose()

    series = client.parse_metric_data(resp)
    assert series[0].values[0] == (1719878400.0, 0.5)
    ts, val = series[0].values[1]
    assert ts == 1719878460.0
    assert math.isnan(val)


@respx.mock
async def test_instant_query_normalizes_tz_naive_time_to_unix() -> None:
    # A tz-naive ISO 'time' would 400 at Prometheus; the client converts it to unix seconds
    # (treating naive as UTC) so instant queries match the range path's behaviour.
    route = respx.get(f"{BASE}/api/v1/query").mock(
        return_value=httpx.Response(200, json=_EMPTY_VECTOR)
    )
    client = _client()
    await client.instant_query("up", time="2024-01-20T11:00:00")
    await client.aclose()
    expected = str(int(datetime(2024, 1, 20, 11, 0, 0, tzinfo=UTC).timestamp()))
    assert route.calls.last.request.url.params["time"] == expected


@respx.mock
async def test_short_query_uses_get_long_query_uses_post() -> None:
    get_route = respx.get(f"{BASE}/api/v1/query").mock(
        return_value=httpx.Response(200, json=_EMPTY_VECTOR)
    )
    post_route = respx.post(f"{BASE}/api/v1/query").mock(
        return_value=httpx.Response(200, json=_EMPTY_VECTOR)
    )
    client = _client()

    await client.instant_query("up")  # short -> GET
    assert get_route.called
    assert not post_route.called

    await client.instant_query("x" * 1100)  # long -> POST
    await client.aclose()
    assert post_route.called


@respx.mock
async def test_promql_error_is_mapped_not_raised() -> None:
    respx.get(f"{BASE}/api/v1/query").mock(
        return_value=httpx.Response(
            400,
            json={"status": "error", "errorType": "bad_data", "error": "parse error: unexpected"},
        )
    )
    client = _client()
    resp = await client.instant_query("this is not promql")
    await client.aclose()

    assert not resp.success
    assert resp.error_type == "bad_data"
    assert "parse error" in (resp.error_message or "")
    assert resp.http_status == 400


@respx.mock
async def test_transport_error_is_mapped_not_raised() -> None:
    respx.get(f"{BASE}/api/v1/query").mock(side_effect=httpx.ConnectError("refused"))
    client = _client()
    resp = await client.instant_query("up")
    await client.aclose()

    assert not resp.success
    assert resp.error_type == "ConnectError"
    assert resp.query_url == f"{BASE}/api/v1/query"


@respx.mock
async def test_label_values_success_and_error() -> None:
    route = respx.get(f"{BASE}/api/v1/label/__name__/values")
    route.mock(return_value=httpx.Response(200, json={"status": "success", "data": ["up", "cpu"]}))
    client = _client()
    assert await client.label_values() == ["up", "cpu"]

    route.mock(return_value=httpx.Response(500, text="boom"))
    assert await client.label_values() == []
    await client.aclose()


@respx.mock
async def test_query_multiple_keys_by_name() -> None:
    respx.get(f"{BASE}/api/v1/query_range").mock(
        return_value=httpx.Response(
            200, json={"status": "success", "data": {"resultType": "matrix", "result": []}}
        )
    )
    client = _client()
    out = await client.query_multiple(
        [("cpu", "cpu_q"), ("mem", "mem_q")], "1719878400", "1719878460", "60s"
    )
    await client.aclose()

    assert set(out) == {"cpu", "mem"}
    assert all(r.success for r in out.values())


def test_iso_timestamp_conversion() -> None:
    # numeric passes through; ISO-8601 converts to unix seconds
    assert PrometheusClient._to_unix_timestamp("1719878400") == "1719878400"
    converted = PrometheusClient._to_unix_timestamp("2024-07-02T00:00:00+00:00")
    assert converted.isdigit()


def test_naive_iso_timestamp_is_treated_as_utc() -> None:
    # A tz-naive ISO string must convert as UTC (not host-local), matching its +00:00 / Z forms,
    # so range windows are not shifted by the deploy host's UTC offset.
    naive = PrometheusClient._to_unix_timestamp("2026-07-01T00:00:00")
    aware = PrometheusClient._to_unix_timestamp("2026-07-01T00:00:00+00:00")
    zulu = PrometheusClient._to_unix_timestamp("2026-07-01T00:00:00Z")
    assert naive == aware == zulu


async def test_parse_scalar_result_degrades_to_empty() -> None:
    # scalar/string PromQL returns a flat [ts, "value"] payload — parse must not raise.
    client = _client()
    resp = PrometheusResponse(
        success=True, data={"resultType": "scalar", "result": [1719878400, "1"]}
    )
    assert client.parse_metric_data(resp) == []
    await client.aclose()


async def test_query_multiple_drops_unexpectedly_failing_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # HTTP errors are folded into PrometheusResponse, so the drop-branch only fires on an
    # unexpected (non-HTTP) raise: the failing name is dropped, the sibling still returns.
    client = _client()

    async def flaky(query: str, start: str, end: str, step: str) -> PrometheusResponse:
        if query == "boom_q":
            raise RuntimeError("kaboom")
        return PrometheusResponse(success=True)

    monkeypatch.setattr(client, "range_query", flaky)
    out = await client.query_multiple([("ok", "ok_q"), ("boom", "boom_q")], "0", "60", "60s")
    await client.aclose()

    assert set(out) == {"ok"}
    assert out["ok"].success


def test_build_prometheus_client_sets_bearer_header() -> None:
    settings = Settings(prometheus_url="http://p.internal:9090", prometheus_token="tok-123")
    client = build_prometheus_client(settings)
    assert client._base_url == "http://p.internal:9090"
    assert client._client.headers.get("authorization") == "Bearer tok-123"


@pytest.mark.parametrize("token", [None])
def test_build_prometheus_client_without_token(token: str | None) -> None:
    settings = Settings(prometheus_url="http://p.internal:9090", prometheus_token=token)
    client = build_prometheus_client(settings)
    assert "authorization" not in client._client.headers
