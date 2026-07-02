"""Shared OpenAI model builder: reasoning-model detection + gpt-5 parameter handling.

No network — ``ChatOpenAI`` construction and request-payload assembly are offline, so these prove
the gpt-5.5 wiring (custom temperature dropped, ``reasoning_effort`` gated to reasoning models)
without an API key or a real call.
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage
from pydantic import SecretStr

from src.agents._llm import build_chat_openai, is_reasoning_model


def test_is_reasoning_model_detects_gpt5_and_o_series() -> None:
    assert is_reasoning_model("gpt-5.5")
    assert is_reasoning_model("gpt-5.4-mini")
    assert is_reasoning_model("o3-mini")
    assert is_reasoning_model("o1")
    # gpt-5-chat is the non-reasoning chat variant; plain gpt-4o is not a reasoning model.
    assert not is_reasoning_model("gpt-5-chat")
    assert not is_reasoning_model("gpt-4o")
    assert not is_reasoning_model("gpt-4o-mini")


def test_reasoning_effort_forwarded_only_for_reasoning_models() -> None:
    reasoning = build_chat_openai(
        model="gpt-5.4-mini", api_key=SecretStr("sk-x"), reasoning_effort="minimal"
    )
    assert reasoning.reasoning_effort == "minimal"
    # Forwarding reasoning_effort to gpt-4o would 400 server-side, so the builder must drop it.
    non_reasoning = build_chat_openai(
        model="gpt-4o", api_key=SecretStr("sk-x"), reasoning_effort="minimal"
    )
    assert non_reasoning.reasoning_effort is None


def test_gpt5_drops_custom_temperature_so_it_never_400s() -> None:
    m = build_chat_openai(
        model="gpt-5.5", api_key=SecretStr("sk-x"), temperature=0.0, max_tokens=8192
    )
    # langchain-openai strips a non-default temperature for gpt-5 (non-chat)...
    assert m.temperature is None
    payload = m._get_request_payload([HumanMessage(content="hi")])
    assert "temperature" not in payload  # ...so it is never sent to the API
    # max_tokens is mapped to the reasoning-model budget parameter (combined reasoning + output).
    assert payload["max_completion_tokens"] == 8192


def test_non_reasoning_model_keeps_temperature() -> None:
    m = build_chat_openai(model="gpt-4o", api_key=SecretStr("sk-x"), temperature=0.0)
    assert m.temperature == 0.0
