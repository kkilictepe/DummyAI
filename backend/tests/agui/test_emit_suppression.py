"""Regression: the guardrail's ``emit-messages``/``emit-tool-calls`` metadata keeps its model
output off the AG-UI wire.

This locks in the mechanism the guardrail depends on: a model call tagged with
``GUARD_CALL_CONFIG`` produces NO TEXT_MESSAGE_* events, while an untagged call does. If a future
``ag_ui_langgraph`` bump changes the metadata keys, this test fails instead of the guard silently
streaming raw classification JSON to users.
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from ag_ui.core import RunAgentInput, UserMessage
from ag_ui_langgraph import LangGraphAgent
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from src.agents.guardrail import GUARD_CALL_CONFIG
from tests.fakes import ScriptedChatModel


class _S(TypedDict):
    messages: Annotated[list, add_messages]


async def _run(node: Any) -> list[str]:
    g = StateGraph(_S)
    g.add_node("n", node)
    g.add_edge(START, "n")
    g.add_edge("n", END)
    agent = LangGraphAgent(name="t", graph=g.compile(checkpointer=InMemorySaver())).clone()
    run_input = RunAgentInput(
        thread_id="t1",
        run_id="r1",
        state={},
        messages=[UserMessage(id="m1", role="user", content="hi")],
        tools=[],
        context=[],
        forwarded_props={},
    )
    return [ev.type.value async for ev in agent.run(run_input)]


async def test_tagged_model_call_is_not_streamed_to_ui() -> None:
    model = ScriptedChatModel(responses=[AIMessage(content="secret classifier json", id="g1")])

    async def guarded(state: _S) -> dict:
        await model.ainvoke(state["messages"], config=GUARD_CALL_CONFIG)
        return {}

    types = await _run(guarded)
    assert "TEXT_MESSAGE_CONTENT" not in types


async def test_untagged_model_call_is_streamed() -> None:
    model = ScriptedChatModel(responses=[AIMessage(content="visible answer", id="v1")])

    async def visible(state: _S) -> dict:
        resp = await model.ainvoke(state["messages"])
        return {"messages": [resp]}

    types = await _run(visible)
    assert "TEXT_MESSAGE_CONTENT" in types
