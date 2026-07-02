"""Health endpoint + app-factory smoke tests."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.clients import close_clients, set_clients
from src.main import create_app


class _FakeProm:
    def __init__(self, success: bool = True, raise_exc: bool = False) -> None:
        self._success = success
        self._raise = raise_exc

    async def instant_query(self, query: str) -> Any:
        if self._raise:
            raise RuntimeError("prometheus down")
        return SimpleNamespace(success=self._success)

    async def aclose(self) -> None:
        pass


class _FakeES:
    def __init__(self, status: str = "green", raise_exc: bool = False) -> None:
        self._status = status
        self._raise = raise_exc

    async def health(self) -> dict[str, str]:
        if self._raise:
            raise RuntimeError("es down")
        return {"status": self._status}

    async def close(self) -> None:
        pass


@pytest.fixture
async def _reset_clients() -> Any:
    """Ensure the module-level client registry starts and ends empty around a deep-health test."""
    await close_clients()
    yield
    await close_clients()


async def test_health_returns_ok() -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["environment"] == "development"
    # Request-id middleware echoes a correlation id on every response.
    assert resp.headers.get("x-request-id")


async def test_request_id_is_propagated() -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health", headers={"x-request-id": "fixed-rid-123"})

    assert resp.headers["x-request-id"] == "fixed-rid-123"


async def _get_deep(app: Any) -> Any:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/health", params={"deep": "1"})


async def test_deep_health_all_ok(_reset_clients: Any) -> None:
    set_clients(_FakeProm(success=True), _FakeES(status="green"))  # type: ignore[arg-type]
    resp = await _get_deep(create_app())

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["dependencies"] == {"prometheus": "ok", "elasticsearch": "ok"}


async def test_deep_health_degraded_when_prometheus_query_fails(_reset_clients: Any) -> None:
    set_clients(_FakeProm(success=False), _FakeES(status="green"))  # type: ignore[arg-type]
    body = (await _get_deep(create_app())).json()

    assert body["status"] == "degraded"
    assert body["dependencies"]["prometheus"] == "degraded"
    assert body["dependencies"]["elasticsearch"] == "ok"


async def test_deep_health_unavailable_when_dependency_raises(_reset_clients: Any) -> None:
    set_clients(_FakeProm(raise_exc=True), _FakeES(raise_exc=True))  # type: ignore[arg-type]
    body = (await _get_deep(create_app())).json()

    assert body["status"] == "degraded"
    assert body["dependencies"] == {
        "prometheus": "unavailable",
        "elasticsearch": "unavailable",
    }


async def test_deep_health_unavailable_when_clients_uninitialised(_reset_clients: Any) -> None:
    # _reset_clients cleared the registry; do not set any clients.
    body = (await _get_deep(create_app())).json()
    assert body["status"] == "degraded"
    assert body["dependencies"]["prometheus"] == "unavailable"
    assert body["dependencies"]["elasticsearch"] == "unavailable"
