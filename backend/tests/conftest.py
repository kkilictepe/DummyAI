"""Shared test fixtures."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from src.config import Settings, reset_config_caches

# Env keys (secrets + nested app config) that could leak the developer's real environment —
# including a committed-locally ``backend/.env`` — into config assertions. Neutralised per test.
_ISOLATED_ENV_KEYS = (
    "ENVIRONMENT",
    "LOG_LEVEL",
    "LLM__ANSWER_MODEL",
    "LLM__GUARD_MODEL",
    "LLM__TEMPERATURE",
    "LLM__MAX_TOKENS",
    "LLM__ANSWER_REASONING_EFFORT",
    "LLM__GUARD_REASONING_EFFORT",
    "COPILOT__MAX_TOOL_ITERATIONS",
    "CORS__ALLOW_ORIGINS",
    "ELASTICSEARCH__INDEX_NAME",
    "PROMETHEUS_URL",
    "PROMETHEUS_TOKEN",
    "ELASTICSEARCH_HOSTS",
    "ELASTICSEARCH_API_KEY",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_BASE_URL",
    "OPENAI_API_KEY",
    "DUMMYAI_CONFIG_DIR",
)


@pytest.fixture(autouse=True)
def _isolate_config(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Make config tests hermetic and deterministic.

    Two ambient sources would otherwise bleed into assertions: the developer's real
    ``backend/.env`` (loaded via ``env_file``) and exported shell vars. We drop the ``.env``
    layer and delete the relevant keys so every test observes ``config.yaml`` / defaults —
    unless the test explicitly sets a value (``monkeypatch.setenv`` / re-points ``env_file``),
    which still wins because it runs after this autouse setup on the same ``monkeypatch``.
    """
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for key in _ISOLATED_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    reset_config_caches()
    yield
    reset_config_caches()
