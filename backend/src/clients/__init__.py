"""Transport clients (Prometheus, Elasticsearch) + process-wide accessors.

LangGraph tool nodes receive no ``Request``, so ``app.state`` is unreachable from tools. The
lifespan builds the two shared clients once and registers them here via :func:`set_clients`;
tools and handlers fetch them with :func:`get_prometheus_client` / :func:`get_es_client`.
"""

from __future__ import annotations

from src.clients.elasticsearch import ElasticsearchClient, build_es_client
from src.clients.prometheus import PrometheusClient, build_prometheus_client
from src.logging import get_logger

_log = get_logger(__name__)

_prometheus_client: PrometheusClient | None = None
_es_client: ElasticsearchClient | None = None


def set_clients(prometheus: PrometheusClient, es: ElasticsearchClient) -> None:
    """Register the shared clients (called once from the lifespan on startup)."""
    global _prometheus_client, _es_client
    _prometheus_client = prometheus
    _es_client = es


def get_prometheus_client() -> PrometheusClient:
    """Return the shared Prometheus client, or raise if the lifespan has not run."""
    if _prometheus_client is None:
        raise RuntimeError("Prometheus client not initialised — did the app lifespan start?")
    return _prometheus_client


def get_es_client() -> ElasticsearchClient:
    """Return the shared Elasticsearch client, or raise if the lifespan has not run."""
    if _es_client is None:
        raise RuntimeError("Elasticsearch client not initialised — did the app lifespan start?")
    return _es_client


async def close_clients() -> None:
    """Close both shared clients and clear the registry (called on shutdown).

    The two closes are independent: the registry is cleared first, then each client is closed in
    its own guard so a failure in one cannot leak the other or prevent Langfuse from flushing.
    """
    global _prometheus_client, _es_client
    prometheus, es = _prometheus_client, _es_client
    _prometheus_client = None
    _es_client = None

    if prometheus is not None:
        try:
            await prometheus.aclose()
        except Exception as exc:
            _log.warning("prometheus_close_failed", error=str(exc))
    if es is not None:
        try:
            await es.close()
        except Exception as exc:
            _log.warning("elasticsearch_close_failed", error=str(exc))


__all__ = [
    "ElasticsearchClient",
    "PrometheusClient",
    "build_es_client",
    "build_prometheus_client",
    "close_clients",
    "get_es_client",
    "get_prometheus_client",
    "set_clients",
]
