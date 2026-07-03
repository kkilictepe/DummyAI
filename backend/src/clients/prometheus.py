"""Async Prometheus client over one shared ``httpx.AsyncClient``.

Ported from the reference ``PrometheusQueryClient`` (``clients/prometheus_query_client.py``),
dropping the ``BaseClient``/registry/instrumentation layers — Dummy AI has exactly one
Prometheus, so there is no per-system routing. The response DTOs (:class:`PrometheusResponse`,
:class:`MetricData`) are kept faithful so the Phase 4 advanced-query normalizer ports unchanged.

``system_id`` is a PromQL label value, never a routing key: callers embed it in the query
(``metric{system_id="KHP"}``), they do not select a client with it.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import perf_counter
from typing import TYPE_CHECKING, Any

import httpx

from src.logging import get_logger

if TYPE_CHECKING:
    from src.config import Settings

_log = get_logger(__name__)

# Prometheus accepts the query in the URL (GET) or form-encoded (POST). Long PromQL blows past
# proxy/server URL length limits, so switch to POST once the query grows past this many chars.
_POST_QUERY_THRESHOLD = 1000


# ---------------------------------------------------------------------------
# Response DTOs (kept faithful to the reference so downstream parsers port 1:1)
# ---------------------------------------------------------------------------


@dataclass
class PrometheusResponse:
    """Structured result of a Prometheus API call."""

    success: bool
    data: dict[str, Any] | None = None
    error_type: str | None = None
    error_message: str | None = None
    warnings: list[str] = field(default_factory=list)
    execution_time_ms: float = 0.0
    http_status: int = 0
    query_url: str = ""


@dataclass
class MetricData:
    """One parsed series: metric name, labels, and ``(timestamp, value)`` points."""

    metric_name: str
    labels: dict[str, str]
    values: list[tuple[float, float]]


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class PrometheusClient:
    """Async HTTP client for Prometheus PromQL endpoints.

    Owns a single shared ``httpx.AsyncClient``. All query methods return a
    :class:`PrometheusResponse` and never raise for HTTP/PromQL errors — the failure is
    reported in ``success``/``error_*`` so callers can degrade gracefully.
    """

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        org_id: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        headers: dict[str, str] = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if org_id:
            headers["X-Scope-OrgID"] = org_id
        self._client = httpx.AsyncClient(base_url=self._base_url, headers=headers, timeout=timeout)

    async def aclose(self) -> None:
        """Close the underlying HTTP client (call on shutdown)."""
        await self._client.aclose()

    # ------------------------------------------------------------------
    # PromQL operations
    # ------------------------------------------------------------------

    async def instant_query(self, query: str, time: str | None = None) -> PrometheusResponse:
        """Execute an instant query against ``/api/v1/query``.

        ``time`` is normalized to Unix seconds (like ``range_query``'s start/end): Prometheus
        rejects a tz-naive RFC3339 string on the ``time`` param, so a bare ISO timestamp without
        an offset would otherwise 400 for instant queries while working for range queries.
        """
        params: dict[str, Any] = {"query": query}
        if time is not None:
            params["time"] = self._to_unix_timestamp(time)
        return await self._execute_query("/api/v1/query", params)

    async def range_query(self, query: str, start: str, end: str, step: str) -> PrometheusResponse:
        """Execute a range query against ``/api/v1/query_range``."""
        params: dict[str, Any] = {
            "query": query,
            "start": self._to_unix_timestamp(start),
            "end": self._to_unix_timestamp(end),
            "step": step,
        }
        return await self._execute_query("/api/v1/query_range", params)

    async def label_values(
        self, label: str = "__name__", *, match: list[str] | None = None
    ) -> list[str]:
        """Fetch distinct values of ``label`` (default: every metric name).

        Maps to ``GET /api/v1/label/<label>/values``. Used for app-server discovery and
        ``monitoring_context`` validation. ``match`` is an optional list of PromQL series
        selectors (e.g. ``['{system_id="KHP"}']``) sent as repeated ``match[]`` params so the
        returned values are scoped to those series (Prometheus >= 2.24; httpx encodes the list as
        ``match[]=<a>&match[]=<b>`` natively). Returns ``[]`` on any transport/parse error rather
        than raising.
        """
        params = {"match[]": match} if match else None
        try:
            response = await self._client.get(f"/api/v1/label/{label}/values", params=params)
        except httpx.HTTPError as exc:
            _log.warning("prometheus_label_values_failed", label=label, error=str(exc))
            return []
        try:
            data = response.json()
        except ValueError as exc:
            _log.warning(
                "prometheus_label_values_failed",
                label=label,
                reason="parse_error",
                http_status=response.status_code,
                error=str(exc),
            )
            return []
        if data.get("status") != "success":
            _log.warning(
                "prometheus_label_values_failed",
                label=label,
                reason="status_not_success",
                error_type=data.get("errorType"),
                http_status=response.status_code,
            )
            return []
        values = data.get("data", [])
        return list(values) if isinstance(values, list) else []

    async def query_multiple(
        self,
        queries: list[tuple[str, str]],
        start: str,
        end: str,
        step: str,
    ) -> dict[str, PrometheusResponse]:
        """Execute multiple range queries concurrently, keyed by name.

        A name whose query raises unexpectedly is dropped from the result (best-effort).
        """

        async def _one(name: str, q: str) -> tuple[str, PrometheusResponse]:
            return name, await self.range_query(q, start, end, step)

        results = await asyncio.gather(
            *(_one(name, q) for name, q in queries), return_exceptions=True
        )

        out: dict[str, PrometheusResponse] = {}
        for item in results:
            if isinstance(item, BaseException):
                _log.warning("prometheus_query_dropped", error=str(item))
                continue
            name, resp = item
            out[name] = resp
        return out

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def parse_metric_data(self, response: PrometheusResponse) -> list[MetricData]:
        """Parse a response into series. Handles instant (vector) and range (matrix) results.

        Values arrive as strings and are float-cast; ``"NaN"`` becomes ``float("nan")``.
        """
        if not response.success or not response.data:
            return []

        result_type = response.data.get("resultType", "")
        results = response.data.get("result", [])
        out: list[MetricData] = []

        for r in results:
            # scalar/string results have a flat [ts, "value"] payload, not a series dict;
            # degrade gracefully instead of raising AttributeError on the non-dict entries.
            if not isinstance(r, dict):
                continue
            metric_info = r.get("metric", {})
            metric_name = metric_info.get("__name__", "unknown")
            labels = {k: v for k, v in metric_info.items() if k != "__name__"}

            if result_type == "matrix":
                values = [self._point(v) for v in r.get("values", [])]
            elif result_type == "vector":
                values = [self._point(r.get("value", [0, "0"]))]
            else:
                values = []

            out.append(MetricData(metric_name=metric_name, labels=labels, values=values))

        return out

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _execute_query(self, endpoint: str, params: dict[str, Any]) -> PrometheusResponse:
        query = str(params.get("query", ""))
        use_post = len(query) > _POST_QUERY_THRESHOLD
        _log.debug("prometheus_query", endpoint=endpoint, method="POST" if use_post else "GET")
        start = perf_counter()
        try:
            if use_post:
                response = await self._client.post(endpoint, data=params)
            else:
                response = await self._client.get(endpoint, params=params)
        except httpx.HTTPError as exc:
            _log.warning(
                "prometheus_query_failed",
                endpoint=endpoint,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return PrometheusResponse(
                success=False,
                error_type=type(exc).__name__,
                error_message=str(exc),
                execution_time_ms=(perf_counter() - start) * 1000,
                query_url=f"{self._base_url}{endpoint}",
            )

        execution_ms = (perf_counter() - start) * 1000
        try:
            data = response.json()
        except ValueError as exc:
            _log.warning(
                "prometheus_response_parse_failed",
                http_status=response.status_code,
                error=str(exc),
            )
            return PrometheusResponse(
                success=False,
                error_type="parse_error",
                error_message=f"Failed to parse response: {exc}",
                execution_time_ms=execution_ms,
                http_status=response.status_code,
                query_url=str(response.url),
            )

        if data.get("status") == "success":
            return PrometheusResponse(
                success=True,
                data=data.get("data"),
                warnings=data.get("warnings", []),
                execution_time_ms=execution_ms,
                http_status=response.status_code,
                query_url=str(response.url),
            )

        # Prometheus reports PromQL/param errors as a JSON body with status="error".
        _log.warning(
            "prometheus_query_returned_error",
            error_type=data.get("errorType"),
            http_status=response.status_code,
        )
        return PrometheusResponse(
            success=False,
            error_type=data.get("errorType", "unknown"),
            error_message=data.get("error", "Unknown error"),
            warnings=data.get("warnings", []),
            execution_time_ms=execution_ms,
            http_status=response.status_code,
            query_url=str(response.url),
        )

    @staticmethod
    def _point(v: list[Any]) -> tuple[float, float]:
        """Cast a ``[timestamp, "value"]`` pair to floats, tolerating ``"NaN"``."""
        return (float(v[0]), float(v[1]) if v[1] != "NaN" else float("nan"))

    @staticmethod
    def _to_unix_timestamp(time_str: str) -> str:
        """Pass through a numeric timestamp; convert ISO-8601 to Unix seconds.

        A tz-naive ISO string (no offset — what ``datetime.utcnow().isoformat()`` produces) is
        treated as **UTC**, not the host's local time, so range windows are not silently shifted
        by the deploy host's UTC offset.
        """
        try:
            float(time_str)
            return time_str
        except ValueError:
            pass
        dt = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return str(int(dt.timestamp()))


def build_prometheus_client(settings: Settings) -> PrometheusClient:
    """Construct the shared client from settings (called by the lifespan)."""
    token = settings.prometheus_token.get_secret_value() if settings.prometheus_token else None
    return PrometheusClient(settings.prometheus_url, token=token)


__all__ = ["MetricData", "PrometheusClient", "PrometheusResponse", "build_prometheus_client"]
