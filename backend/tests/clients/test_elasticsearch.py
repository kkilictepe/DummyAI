"""ElasticsearchClient wrappers + builder, exercised against a fake transport (no live ES)."""

from __future__ import annotations

from typing import Any

import pytest

from src.clients.elasticsearch import ElasticsearchClient, build_es_client
from src.config import Settings


class _FakeCluster:
    def __init__(self, status: str = "green") -> None:
        self._status = status

    async def health(self) -> dict[str, Any]:
        return {"status": self._status, "cluster_name": "test"}


class _FakeES:
    """Stands in for ``AsyncElasticsearch`` — records calls, returns canned bodies."""

    def __init__(self, status: str = "green") -> None:
        self.calls: list[tuple[str, Any, Any, dict[str, Any]]] = []
        self.closed = False
        self.cluster = _FakeCluster(status)

    async def search(self, index: str, body: dict[str, Any], **kw: Any) -> dict[str, Any]:
        self.calls.append(("search", index, body, kw))
        return {"hits": {"total": {"value": 0}, "hits": []}}

    async def count(self, index: str, body: dict[str, Any], **kw: Any) -> dict[str, Any]:
        self.calls.append(("count", index, body, kw))
        return {"count": 0}

    async def msearch(self, body: list[dict[str, Any]], **kw: Any) -> dict[str, Any]:
        self.calls.append(("msearch", None, body, kw))
        return {"responses": []}

    async def close(self) -> None:
        self.closed = True


def _client_with_fake(status: str = "green") -> tuple[ElasticsearchClient, _FakeES]:
    client = ElasticsearchClient("http://es.test:9200", default_index="sap-logs-*")
    fake = _FakeES(status)
    client._client = fake  # type: ignore[assignment]
    return client, fake


async def test_search_logs_targets_default_index() -> None:
    client, fake = _client_with_fake()
    body = {"query": {"match_all": {}}}
    result = await client.search_logs(body)

    assert result == {"hits": {"total": {"value": 0}, "hits": []}}
    op, index, sent_body, _ = fake.calls[0]
    assert op == "search"
    assert index == "sap-logs-*"
    assert sent_body == body


async def test_search_logs_honours_explicit_index() -> None:
    client, fake = _client_with_fake()
    await client.search_logs({"query": {}}, index="other-*")
    assert fake.calls[0][1] == "other-*"


async def test_count_and_msearch_delegate() -> None:
    client, fake = _client_with_fake()
    assert await client.count("sap-logs-*", {"query": {}}) == {"count": 0}
    assert await client.msearch([{"index": "sap-logs-*"}, {"query": {}}]) == {"responses": []}
    ops = [c[0] for c in fake.calls]
    assert ops == ["count", "msearch"]


async def test_health_reports_cluster_status() -> None:
    client, _ = _client_with_fake(status="yellow")
    health = await client.health()
    assert health["status"] == "yellow"


async def test_close_closes_underlying_client() -> None:
    client, fake = _client_with_fake()
    await client.close()
    assert fake.closed is True


def test_build_es_client_maps_settings() -> None:
    settings = Settings(
        elasticsearch_hosts="http://es.internal:9200",
        elasticsearch_api_key="es-secret",
    )
    client = build_es_client(settings)
    assert client.default_index == settings.elasticsearch.index_name


def test_build_es_client_unwraps_api_key_and_splits_hosts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class _SpyES:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr("elasticsearch.AsyncElasticsearch", _SpyES)
    settings = Settings(
        elasticsearch_hosts="http://a:9200, http://b:9200",
        elasticsearch_api_key="es-secret",
    )
    build_es_client(settings)

    # SecretStr is unwrapped to the raw value (not the SecretStr object) ...
    assert captured["api_key"] == "es-secret"
    # ... and comma-separated hosts are split + stripped into a list.
    assert captured["hosts"] == ["http://a:9200", "http://b:9200"]


def test_build_es_client_omits_api_key_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class _SpyES:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr("elasticsearch.AsyncElasticsearch", _SpyES)
    build_es_client(Settings(elasticsearch_hosts="http://a:9200"))
    assert "api_key" not in captured
