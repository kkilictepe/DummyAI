"""Copilot graph routing + node behaviour, invoked directly (no HTTP, no real LLM).

The guardrail and answering-agent factories are monkeypatched at their ``src.flow.copilot``
lookup site so ``build_copilot_graph`` wires fakes. Tools that need a live client are never
called — the scripted agent either answers directly or calls ``list_sap_systems`` (config-only).
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

import src.flow.copilot as flow
from src.config import get_settings
from src.flow.copilot import REFUSAL_TEXT, _refuse_node, _route, build_copilot_graph
from src.schemas import GuardrailVerdict
from src.tools import get_all_tools
from tests.fakes import make_guard, make_scripted_agent

_CONFIG = {"configurable": {"thread_id": "t-flow"}}
_SAP_OPS = GuardrailVerdict(allowed=True, category="sap_ops", reason="ok")
_OFF_TOPIC = GuardrailVerdict(allowed=False, category="off_topic", reason="unrelated")


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    *,
    verdict: GuardrailVerdict,
    responses: list[AIMessage],
    tools: Any | None = None,
) -> Any:
    """Patch the flow's factories to use a fake guard + scripted agent; return the agent so tests
    can inspect whether its model was invoked."""
    agent = make_scripted_agent(responses, tools if tools is not None else get_all_tools())
    monkeypatch.setattr(
        flow, "build_guardrail_runnable", lambda settings: make_guard(verdict=verdict)
    )
    monkeypatch.setattr(flow, "build_copilot_agent", lambda settings, tools: agent)
    return agent


async def test_off_topic_routes_to_refuse_without_calling_agent_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _wire(monkeypatch, verdict=_OFF_TOPIC, responses=[AIMessage(content="unused")])
    graph = build_copilot_graph(get_settings())

    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="Tell me a joke.", id="h1")], "system_ids": ["KHP"]},
        config=_CONFIG,
    )

    # guardrail is internal-only (excluded from the output schema); read it from the checkpoint.
    internal = (await graph.aget_state(_CONFIG)).values
    assert internal["guardrail"]["category"] == "off_topic"
    assert result["messages"][-1].content == REFUSAL_TEXT
    # The whole point of the guardrail: the answering model is never invoked on a refusal.
    assert agent._scripted_model.calls == 0
    # The internal verdict must NOT be part of the graph's public output.
    assert "guardrail" not in result


async def test_sap_ops_answers_in_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = _wire(
        monkeypatch,
        verdict=_SAP_OPS,
        responses=[AIMessage(content="**KHP** looks healthy.", id="a1")],
    )
    graph = build_copilot_graph(get_settings())

    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="How is KHP?", id="h1")], "system_ids": ["KHP"]},
        config=_CONFIG,
    )

    internal = (await graph.aget_state(_CONFIG)).values
    assert internal["guardrail"]["allowed"] is True
    assert "**KHP**" in result["messages"][-1].content
    assert agent._scripted_model.calls >= 1


async def test_sap_ops_runs_tool_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    # First turn: call list_sap_systems (config-only, no client). Second turn: final answer.
    responses = [
        AIMessage(
            content="",
            tool_calls=[{"name": "list_sap_systems", "args": {}, "id": "call-1"}],
            id="a1",
        ),
        AIMessage(content="Here are the systems.", id="a2"),
    ]
    agent = _wire(monkeypatch, verdict=_SAP_OPS, responses=responses)
    graph = build_copilot_graph(get_settings())

    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="List systems", id="h1")], "system_ids": ["KHP"]},
        config=_CONFIG,
    )

    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert any(m.name == "list_sap_systems" for m in tool_messages)
    assert result["messages"][-1].content == "Here are the systems."
    assert agent._scripted_model.calls == 2


def test_route_allows_only_allowed_verdict() -> None:
    # _route reads the verdict as a plain dict (as the guardrail node writes it).
    assert _route({"guardrail": _SAP_OPS.model_dump()}) == "agent"  # type: ignore[arg-type]
    assert _route({"guardrail": _OFF_TOPIC.model_dump()}) == "refuse"  # type: ignore[arg-type]
    assert _route({"guardrail": None}) == "refuse"  # type: ignore[arg-type]
    assert _route({}) == "refuse"  # type: ignore[arg-type]


async def test_refuse_node_emits_manual_message_event(monkeypatch: pytest.MonkeyPatch) -> None:
    dispatched: list[tuple[str, dict[str, Any]]] = []

    async def _capture(name: str, data: dict[str, Any], *args: Any, **kwargs: Any) -> None:
        dispatched.append((name, data))

    monkeypatch.setattr(flow, "adispatch_custom_event", _capture)

    out = await _refuse_node({"messages": [], "system_ids": [], "guardrail": _OFF_TOPIC})

    # A streamed refusal: exactly one manual-emit event whose id matches the appended AIMessage.
    assert len(dispatched) == 1
    name, data = dispatched[0]
    assert name == "manually_emit_message"
    assert data["message"] == REFUSAL_TEXT
    appended = out["messages"][0]
    assert appended.content == REFUSAL_TEXT
    assert appended.id == data["message_id"]
