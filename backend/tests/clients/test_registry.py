"""Client registry accessors: init guard, teardown, and exception-safe close."""

from __future__ import annotations

import pytest

from src.clients import close_clients, get_es_client, get_prometheus_client, set_clients


class _FakeProm:
    def __init__(self, raise_on_close: bool = False) -> None:
        self.closed = False
        self._raise = raise_on_close

    async def aclose(self) -> None:
        if self._raise:
            raise RuntimeError("aclose boom")
        self.closed = True


class _FakeES:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


async def test_accessors_raise_before_init() -> None:
    await close_clients()  # ensure the registry is empty
    with pytest.raises(RuntimeError, match="not initialised"):
        get_prometheus_client()
    with pytest.raises(RuntimeError, match="not initialised"):
        get_es_client()


async def test_set_and_close_clients_roundtrip() -> None:
    prom, es = _FakeProm(), _FakeES()
    set_clients(prom, es)  # type: ignore[arg-type]
    assert get_prometheus_client() is prom
    assert get_es_client() is es

    await close_clients()
    assert prom.closed and es.closed
    with pytest.raises(RuntimeError):
        get_prometheus_client()


async def test_close_clients_closes_es_even_if_prometheus_close_raises() -> None:
    prom, es = _FakeProm(raise_on_close=True), _FakeES()
    set_clients(prom, es)  # type: ignore[arg-type]

    await close_clients()  # a failing prometheus close must not prevent the ES close

    assert es.closed  # ES still closed despite prometheus.aclose() raising
    with pytest.raises(RuntimeError):  # registry cleared regardless
        get_es_client()
