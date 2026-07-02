"""The Copilot flow graph.

``START → guardrail → route → agent → END`` for allowed SAP-ops turns, else
``START → guardrail → route → refuse → END``. The graph is compiled once at app startup and
reused per request; per-request scope arrives via ``state["system_ids"]`` (injected by the
endpoint from the AG-UI ``RunAgentInput`` before the graph runs).

An ``InMemorySaver`` checkpointer is **required**: the AG-UI adapter (``LangGraphAgent``) calls
``graph.aget_state`` and reconciles the incoming message list against the checkpoint by message
id, so replaying full history each turn does not duplicate messages. This supersedes the plan's
"no checkpointer for v1" note, which is incompatible with the adapter.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Annotated, Any, TypedDict

from langchain_core.callbacks.manager import adispatch_custom_event
from langchain_core.messages import AIMessage, AnyMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from src.agents.copilot_agent import build_copilot_agent
from src.agents.guardrail import build_guardrail_runnable, make_guardrail_node
from src.tools import get_all_tools

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

    from src.config import Settings

# ag_ui_langgraph turns this custom event into a streamed TEXT_MESSAGE_START/CONTENT/END triplet.
_MANUALLY_EMIT_MESSAGE = "manually_emit_message"

REFUSAL_TEXT = (
    "I'm the Dummy AI SAP Basis operations copilot, so I can only help with the health, "
    "performance, and logs of the SAP systems I monitor. I can't help with that request — "
    "try asking about a system's CPU, memory, work processes, dumps, or recent errors."
)


class CopilotState(TypedDict):
    """Copilot graph state. ``messages`` uses the ``add_messages`` reducer; ``system_ids`` scopes
    the answer; ``guardrail`` records the classification (as a plain dict — see the node) for
    routing and observability."""

    messages: Annotated[list[AnyMessage], add_messages]
    system_ids: list[str]
    guardrail: dict[str, Any] | None


class CopilotOutputState(TypedDict):
    """Public output schema. Deliberately excludes ``guardrail`` and ``system_ids``: the AG-UI
    adapter derives its STATE_SNAPSHOT from the graph's *output* keys, so exposing the internal
    ``guardrail`` here would stream the classifier's category + LLM-authored reason to the client
    (leaking guardrail logic / a refusal-tuning oracle). Routing still reads ``guardrail`` from the
    full internal state; only what leaves the graph is narrowed."""

    messages: Annotated[list[AnyMessage], add_messages]


def _route(state: CopilotState) -> str:
    verdict = state.get("guardrail")
    return "agent" if (verdict is not None and verdict.get("allowed")) else "refuse"


async def _refuse_node(state: CopilotState) -> dict[str, Any]:
    """Emit a deterministic refusal with no LLM/tool calls.

    The message is streamed to the UI via the AG-UI manual-emit custom event (so the client sees
    TEXT_MESSAGE_* like any answer) and also appended to state with the *same* id so history and
    the adapter's message-snapshot stay consistent."""
    message_id = f"refusal-{uuid.uuid4().hex}"
    await adispatch_custom_event(
        _MANUALLY_EMIT_MESSAGE,
        {"message_id": message_id, "message": REFUSAL_TEXT},
    )
    return {"messages": [AIMessage(id=message_id, content=REFUSAL_TEXT)]}


def build_copilot_graph(settings: Settings) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Compile the Copilot flow graph. Pure — constructs models/tools but performs no network I/O
    (clients are only touched at tool-invoke time via the module accessors set by the lifespan)."""
    guard = build_guardrail_runnable(settings)
    classify = make_guardrail_node(guard)
    agent = build_copilot_agent(settings, get_all_tools())

    # Thin wrapper so the node's declared input type is the graph state schema (satisfies
    # StateGraph.add_node's typed overloads); the underlying node accepts any message mapping.
    async def guardrail_node(state: CopilotState) -> dict[str, Any]:
        return await classify(dict(state))

    graph: StateGraph[CopilotState, Any, Any, Any] = StateGraph(
        CopilotState, output_schema=CopilotOutputState
    )
    graph.add_node("guardrail", guardrail_node)
    graph.add_node("agent", agent)
    graph.add_node("refuse", _refuse_node)

    graph.add_edge(START, "guardrail")
    graph.add_conditional_edges("guardrail", _route, {"agent": "agent", "refuse": "refuse"})
    graph.add_edge("agent", END)
    graph.add_edge("refuse", END)

    return graph.compile(checkpointer=InMemorySaver())
