"""Async Elasticsearch client wrapping one shared ``AsyncElasticsearch``.

Ported from the reference ``ElasticsearchClient`` (``clients/elasticsearch_client.py``),
dropping the ``BaseClient``/registry, the lazy sync client, and the on-disk debug-dump plumbing —
Dummy AI has exactly one Elasticsearch. The ES9 client requires an ES9 server (pin ``>=9,<10``).

``search_logs`` is the high-level convenience boundary the log tools call (Phase 5) and the
seam that tests stub. ``search``/``count``/``msearch``/``health``/``close`` are thin, typed
wrappers over the underlying client.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.config import Settings


class ElasticsearchClient:
    """Async-first Elasticsearch client over a single shared ``AsyncElasticsearch``."""

    def __init__(
        self,
        hosts: str,
        *,
        api_key: str | None = None,
        default_index: str = "sap-logs-*",
        verify_certs: bool = True,
        request_timeout: float = 30.0,
    ) -> None:
        from elasticsearch import AsyncElasticsearch

        self._default_index = default_index
        host_list = [h.strip() for h in hosts.split(",") if h.strip()]
        kwargs: dict[str, Any] = {
            "hosts": host_list,
            "verify_certs": verify_certs,
            "request_timeout": request_timeout,
        }
        if api_key:
            kwargs["api_key"] = api_key
        self._client = AsyncElasticsearch(**kwargs)

    @property
    def default_index(self) -> str:
        """Index pattern used when a caller does not specify one."""
        return self._default_index

    # ------------------------------------------------------------------
    # Low-level wrappers (``body=`` is a supported raw-request form in ES 8.13+/9.x)
    # ------------------------------------------------------------------

    async def search(self, index: str, body: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        response = await self._client.search(index=index, body=body, **kwargs)
        return dict(response)

    async def count(self, index: str, body: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        response = await self._client.count(index=index, body=body, **kwargs)
        return dict(response)

    async def msearch(self, body: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        response = await self._client.msearch(body=body, **kwargs)
        return dict(response)

    async def health(self) -> dict[str, Any]:
        """Return cluster health (used by ``/health?deep=1``)."""
        response = await self._client.cluster.health()
        return dict(response)

    async def close(self) -> None:
        """Close the underlying client (call on shutdown)."""
        await self._client.close()

    # ------------------------------------------------------------------
    # High-level boundary (stubbed in tests, used by the log tools)
    # ------------------------------------------------------------------

    async def search_logs(
        self, body: dict[str, Any], *, index: str | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        """Run a log search against the default index (or ``index`` when given)."""
        return await self.search(index=index or self._default_index, body=body, **kwargs)


def build_es_client(settings: Settings) -> ElasticsearchClient:
    """Construct the shared client from settings (called by the lifespan)."""
    api_key = (
        settings.elasticsearch_api_key.get_secret_value()
        if settings.elasticsearch_api_key
        else None
    )
    return ElasticsearchClient(
        settings.elasticsearch_hosts,
        api_key=api_key,
        default_index=settings.elasticsearch.index_name,
    )


__all__ = ["ElasticsearchClient", "build_es_client"]
