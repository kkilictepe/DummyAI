"""Shared builders + a fake Prometheus client for the advanced-query tests.

The fake subclasses the real :class:`PrometheusClient` and overrides only the two network
methods the engine calls (``query_multiple`` / ``instant_query``), so the genuine
``parse_metric_data`` still runs. ``query_multiple`` consumes one response-map per call (the last
repeats), which lets a single fake serve baseline_compare's two windows (current then baseline).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from src.clients.prometheus import PrometheusClient, PrometheusResponse
from src.tools.prometheus_advanced_query.schemas import (
    MetricResult,
    MetricSeriesPoint,
    MetricSummary,
    TrendDirection,
)


def iso(ts: float) -> str:
    """Unix seconds -> the ISO-8601 string the normalizer emits (UTC)."""
    return datetime.fromtimestamp(ts, tz=UTC).isoformat()


def matrix(
    name: str, values: list[list[Any]], labels: dict[str, str] | None = None
) -> PrometheusResponse:
    """A single-series matrix (range) response."""
    metric = {"__name__": name, **(labels or {})}
    return PrometheusResponse(
        success=True,
        data={"resultType": "matrix", "result": [{"metric": metric, "values": values}]},
    )


def matrix_multi(
    name: str, series: list[tuple[dict[str, str], list[list[Any]]]]
) -> PrometheusResponse:
    """A multi-series matrix response (one entry per label set)."""
    result = [
        {"metric": {"__name__": name, **labels}, "values": values} for labels, values in series
    ]
    return PrometheusResponse(success=True, data={"resultType": "matrix", "result": result})


def vector(
    name: str, ts: float, value: str, labels: dict[str, str] | None = None
) -> PrometheusResponse:
    """A single-series vector (instant) response."""
    metric = {"__name__": name, **(labels or {})}
    return PrometheusResponse(
        success=True,
        data={"resultType": "vector", "result": [{"metric": metric, "value": [ts, value]}]},
    )


def ramp(values: list[float], start_ts: float = 1000, step: int = 60) -> list[list[Any]]:
    """Build ``[[ts, "value"], ...]`` matrix points from float values."""
    return [[start_ts + i * step, str(v)] for i, v in enumerate(values)]


def make_series_result(
    name: str, values: list[float], start_ts: float = 1000, step: int = 60
) -> MetricResult:
    """Build a ``MetricResult`` with an aligned ISO-timestamped series (pure-component tests)."""
    points = [
        MetricSeriesPoint(timestamp=iso(start_ts + i * step), value=v) for i, v in enumerate(values)
    ]
    summary = MetricSummary(
        min=min(values) if values else 0.0,
        max=max(values) if values else 0.0,
        avg=sum(values) / len(values) if values else 0.0,
        p95=0.0,
        trend=TrendDirection.FLAT,
        data_points=len(values),
    )
    return MetricResult(metric=name, series=points, summary=summary)


class FakeES:
    """Minimal ES stand-in so ``set_clients`` is satisfied (the advanced tool never touches ES)."""

    async def close(self) -> None:
        return None


class FakeProm(PrometheusClient):
    """Real Prometheus client with canned ``query_multiple`` / ``instant_query``."""

    def __init__(
        self,
        range_maps: list[dict[str, PrometheusResponse]],
        instant_map: dict[str, PrometheusResponse] | None = None,
    ) -> None:
        super().__init__("http://prom.test:9090")
        self._range_maps = range_maps
        self._instant_map = instant_map or {}
        self.range_calls: list[tuple[list[tuple[str, str]], str, str, str]] = []
        self.instant_calls: list[tuple[str, str | None]] = []

    async def query_multiple(
        self, queries: list[tuple[str, str]], start: str, end: str, step: str
    ) -> dict[str, PrometheusResponse]:
        idx = min(len(self.range_calls), len(self._range_maps) - 1)
        self.range_calls.append((list(queries), start, end, step))
        table = self._range_maps[idx]
        return {name: table[name] for name, _ in queries if name in table}

    async def instant_query(self, query: str, time: str | None = None) -> PrometheusResponse:
        self.instant_calls.append((query, time))
        base = query.split("{", 1)[0]
        if base in self._instant_map:
            return self._instant_map[base]
        return PrometheusResponse(success=True, data={"resultType": "vector", "result": []})
