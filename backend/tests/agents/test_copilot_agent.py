"""The answering agent's real dynamic prompt: base prompt + per-request scope line.

No existing test covers the actual ``build_copilot_agent`` dynamic prompt — the flow/SSE tests use
the scripted stand-in in :mod:`tests.fakes`. Here we build the *real* agent, swap only the OpenAI
model for a scripted fake, and assert that the system text the model receives is
``<base prompt>\n\n<scope line>`` for both the scoped and the all-systems cases.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from src.agents.copilot_agent import build_copilot_agent
from src.config import Settings
from tests.fakes import ScriptedChatModel

# A distinctive sentinel base prompt decouples this test from the prompt's real wording (its content
# is asserted in tests/prompts/test_copilot_prompt.py); here we only care that the base prompt is
# carried through and the scope line appended. The agent reads COPILOT_SYSTEM_PROMPT as a module
# global, so patching it on copilot_agent swaps the base prompt.
_BASE_MARKER = "BASE-PROMPT-MARKER: SAP Basis operations copilot"


def _patch(monkeypatch: pytest.MonkeyPatch) -> ScriptedChatModel:
    """Swap the base prompt for a sentinel and the OpenAI builder for a scripted model that records
    the system text it was handed (both names are looked up at call time, so setattr is enough)."""
    monkeypatch.setattr("src.agents.copilot_agent.COPILOT_SYSTEM_PROMPT", _BASE_MARKER)
    model = ScriptedChatModel(responses=[AIMessage(content="ok")])
    monkeypatch.setattr("src.agents._llm.build_chat_openai", lambda **kwargs: model)
    return model


async def test_dynamic_prompt_appends_scoped_systems(monkeypatch: pytest.MonkeyPatch) -> None:
    model = _patch(monkeypatch)
    agent = build_copilot_agent(Settings(), tools=[])

    await agent.ainvoke({"messages": [HumanMessage(content="How is KHP?")], "system_ids": ["KHP"]})

    assert model.last_system_text is not None
    # Base prompt is carried through verbatim, with the per-request scope line appended.
    assert _BASE_MARKER in model.last_system_text
    assert "In-scope SAP systems for this conversation: KHP." in model.last_system_text


async def test_dynamic_prompt_falls_back_to_all_systems(monkeypatch: pytest.MonkeyPatch) -> None:
    model = _patch(monkeypatch)
    agent = build_copilot_agent(Settings(), tools=[])

    await agent.ainvoke(
        {"messages": [HumanMessage(content="How is everything?")], "system_ids": None}
    )

    assert model.last_system_text is not None
    assert _BASE_MARKER in model.last_system_text
    # No scoped ids -> the "all monitored systems" line.
    assert "In-scope SAP systems: all monitored systems." in model.last_system_text
