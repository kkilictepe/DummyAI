"""Fixtures for the ES tool tests.

``es_patch`` installs a :class:`FakeES` behind every tool's ``get_es_client()`` accessor, so tests
can drive the real ``@tool`` entry point (schema validation + wiring) with a fake transport.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from src.tools.elasticsearch import (
    tool_es_aggregation,
    tool_es_cluster_errors,
    tool_es_compare_windows,
    tool_es_drilldown_around,
    tool_es_field_search,
    tool_es_raw_query,
)

from ._helpers import FakeES

_TOOL_MODULES = (
    tool_es_field_search,
    tool_es_aggregation,
    tool_es_compare_windows,
    tool_es_drilldown_around,
    tool_es_raw_query,
    tool_es_cluster_errors,
)


@pytest.fixture
def es_patch(monkeypatch: pytest.MonkeyPatch) -> Callable[[FakeES], FakeES]:
    """Return an installer: ``fake = es_patch(FakeES(...))`` wires it into every ES tool."""

    def install(fake: FakeES) -> FakeES:
        for module in _TOOL_MODULES:
            monkeypatch.setattr(module, "get_es_client", lambda fake=fake: fake)
        return fake

    return install
