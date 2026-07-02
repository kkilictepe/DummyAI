"""Shared test doubles for the Copilot flow (Phase 3).

``GenericFakeChatModel`` cannot drive ``create_agent`` (no ``bind_tools``; it also can't stream an
empty-content tool-call turn), so we use a purpose-built scripted model that:

* survives ``create_agent``'s ``model.bind_tools()`` (returns self),
* streams content token-by-token AND emits ``tool_call_chunks`` (so the AG-UI adapter produces
  TEXT_MESSAGE_* and TOOL_CALL_* respectively), splitting tool args across chunks like a real
  provider so TOOL_CALL_ARGS is exercised.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from typing import Any

from langchain.agents import create_agent
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.runnables import Runnable, RunnableLambda

from src.agents.copilot_agent import CopilotAgentState
from src.schemas import GuardrailVerdict


class ScriptedChatModel(BaseChatModel):
    """Replays scripted AIMessages in order (repeating the last), streaming content and tool
    calls. ``calls`` counts how many turns were consumed (0 means the model was never invoked)."""

    responses: list[AIMessage]
    calls: int = 0
    # System prompt text of the most recent invocation — lets tests assert what the answering
    # agent actually received (e.g. that per-request scope reached the prompt).
    last_system_text: str | None = None

    @property
    def _llm_type(self) -> str:
        return "scripted-fake"

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        return self

    def _capture(self, messages: list[BaseMessage]) -> None:
        for message in messages:
            if isinstance(message, SystemMessage) and isinstance(message.content, str):
                self.last_system_text = message.content

    def _next(self) -> AIMessage:
        message = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return message

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self._capture(messages)
        return ChatResult(generations=[ChatGeneration(message=self._next())])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        self._capture(messages)
        message = self._next()
        for index, call in enumerate(message.tool_calls):
            args_json = json.dumps(call["args"])
            mid = max(1, len(args_json) // 2)
            # Mirror real providers: first chunk announces name+id with no args yet, then the args
            # stream as deltas -> TOOL_CALL_START followed by reconstructable TOOL_CALL_ARGS.
            fragments = [
                {"name": call["name"], "args": "", "id": call.get("id"), "index": index},
                {"name": None, "args": args_json[:mid], "id": None, "index": index},
                {"name": None, "args": args_json[mid:], "id": None, "index": index},
            ]
            for fragment in fragments:
                chunk = ChatGenerationChunk(
                    message=AIMessageChunk(content="", tool_call_chunks=[fragment])
                )
                if run_manager:
                    run_manager.on_llm_new_token("", chunk=chunk)
                yield chunk
        content = message.content if isinstance(message.content, str) else ""
        if content:
            for token in content.split(" "):
                chunk = ChatGenerationChunk(
                    message=AIMessageChunk(content=token + " ", id=message.id)
                )
                if run_manager:
                    run_manager.on_llm_new_token(token, chunk=chunk)
                yield chunk


def make_scripted_agent(responses: list[AIMessage], tools: Sequence[Any]) -> Any:
    """Build an answering-agent subgraph identical in shape to ``build_copilot_agent`` but backed
    by a scripted model, so flow/SSE tests never call a real LLM."""
    model = ScriptedChatModel(responses=responses)

    from langchain.agents.middleware import dynamic_prompt

    @dynamic_prompt
    def prompt(request: Any) -> str:
        ids = request.state.get("system_ids") or []
        return "TEST PROMPT. scope=" + (",".join(ids) if ids else "ALL")

    agent = create_agent(
        model=model,
        tools=list(tools),
        middleware=[prompt],
        state_schema=CopilotAgentState,
        name="copilot_agent",
    )
    # Expose the model so tests can assert whether it was invoked (refusal path must not call it).
    agent._scripted_model = model  # type: ignore[attr-defined]
    return agent


def make_guard(
    *,
    verdict: GuardrailVerdict | None = None,
    error: Exception | None = None,
) -> Runnable[Any, GuardrailVerdict]:
    """A fake guardrail runnable that returns a fixed verdict or raises (to test fail-open)."""

    def _call(_messages: Any) -> GuardrailVerdict:
        if error is not None:
            raise error
        assert verdict is not None
        return verdict

    return RunnableLambda(_call)
