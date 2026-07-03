"""The answering agent for the Copilot flow.

A LangChain ``create_agent`` (NOT the legacy ``create_react_agent``) bound to the curated SAP
tool roster. Two deliberate points:

* **No ``response_format``.** CLAUDE.md says agents return structured output, but a chat copilot
  must stream Markdown tokens. We resolve the tension by keeping structure in *graph state* (the
  ``GuardrailVerdict``) and letting this agent stream free Markdown. Forcing a ``response_format``
  here would pin the final turn to a schema instead of streamable prose, so we deliberately omit it.
* **Per-request scope via a dynamic prompt.** The in-scope ``system_ids`` differ per request, but
  the graph is compiled once at startup. A ``dynamic_prompt`` middleware reads ``system_ids`` from
  state at invoke time and appends the scope line to the committed base prompt.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain.agents import create_agent
from langchain.agents.middleware import AgentState, dynamic_prompt

from src.logging import get_logger
from src.prompts.copilot import COPILOT_SYSTEM_PROMPT

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool
    from langgraph.graph.state import CompiledStateGraph

    from src.config import Settings

_log = get_logger(__name__)


class CopilotAgentState(AgentState):
    """Answering-agent state = the standard agent state plus the in-scope system ids.

    Declaring ``system_ids`` here is what lets the nested agent subgraph receive the parent
    graph's ``system_ids`` channel, so the dynamic prompt can read it."""

    system_ids: list[str]


def _scope_line(system_ids: list[str] | None) -> str:
    if system_ids:
        return "In-scope SAP systems for this conversation: " + ", ".join(system_ids) + "."
    return "In-scope SAP systems: all monitored systems."


def build_copilot_agent(
    settings: Settings, tools: list[BaseTool]
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Build the answering agent subgraph. Compiled without a checkpointer — the parent Copilot
    graph owns persistence, and a nested subgraph shares it."""
    from src.agents._llm import build_chat_openai

    model = build_chat_openai(
        model=settings.llm.answer_model,
        api_key=settings.openai_api_key,
        temperature=settings.llm.temperature,
        max_tokens=settings.llm.max_tokens,
        reasoning_effort=settings.llm.answer_reasoning_effort,
    )

    @dynamic_prompt
    def scoped_prompt(request: Any) -> str:
        system_ids = request.state.get("system_ids")
        return f"{COPILOT_SYSTEM_PROMPT}\n\n{_scope_line(system_ids)}"

    agent = create_agent(
        model=model,
        tools=list(tools),
        middleware=[scoped_prompt],
        state_schema=CopilotAgentState,
        name="copilot_agent",
    )
    _log.debug(
        "copilot_agent_built",
        model=settings.llm.answer_model,
        tool_count=len(tools),
    )
    return agent
