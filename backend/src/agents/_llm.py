"""Shared OpenAI chat-model construction for the agent flows.

Both the answering agent and the guardrail build a ``ChatOpenAI`` the same way, so the
provider-specific knowledge lives here in one place — in particular the GPT-5 / o-series
**reasoning model** handling:

* ``reasoning_effort`` (``'minimal' | 'low' | 'medium' | 'high'``) is a *reasoning-model-only*
  parameter. Sending it to a non-reasoning model (e.g. ``gpt-4o``) is a 400, so we only forward it
  when the target model is a reasoning model.
* A custom ``temperature`` is **ignored** by gpt-5 (non-chat) / o1 models — ``langchain-openai``
  itself drops a non-default temperature for those, so we can pass the configured value verbatim and
  let the library reconcile it. ``max_tokens`` is likewise mapped to ``max_completion_tokens`` by
  the library (and for reasoning models that budget is shared by reasoning **and** visible output).

``ChatOpenAI`` is imported lazily so importing an agent module never requires the OpenAI SDK to be
importable in a bare environment (graph construction happens at app startup, not import time).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langchain_openai import ChatOpenAI
    from pydantic import SecretStr


def is_reasoning_model(model: str) -> bool:
    """True for OpenAI reasoning families that accept ``reasoning_effort`` and ignore a custom
    ``temperature``: ``gpt-5*`` (except ``gpt-5-chat``) plus the o1/o3/o4 series."""
    m = model.lower()
    if m.startswith(("o1", "o3", "o4")):
        return True
    return m.startswith("gpt-5") and "chat" not in m


def build_chat_openai(
    *,
    model: str,
    api_key: SecretStr | None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    reasoning_effort: str | None = None,
) -> ChatOpenAI:
    """Construct a ``ChatOpenAI`` with only the parameters the target model actually supports.

    ``reasoning_effort`` is forwarded only for reasoning models; every other value is passed through
    (``langchain-openai`` drops a non-default temperature for gpt-5/o1 and maps ``max_tokens`` to
    ``max_completion_tokens`` itself)."""
    from langchain_openai import ChatOpenAI

    kwargs: dict[str, Any] = {"model": model, "api_key": api_key}
    if temperature is not None:
        kwargs["temperature"] = temperature
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if reasoning_effort is not None and is_reasoning_model(model):
        kwargs["reasoning_effort"] = reasoning_effort
    return ChatOpenAI(**kwargs)


__all__ = ["build_chat_openai", "is_reasoning_model"]
