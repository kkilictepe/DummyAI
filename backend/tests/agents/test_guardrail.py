"""Guardrail node: classification wiring, message extraction, and the fail-open policy.

The real ``ChatOpenAI`` guard is never constructed here — ``make_guardrail_node`` is fed a
fake runnable, so these tests are hermetic and exercise the node's own logic (extraction,
error handling, verdict plumbing) rather than the model.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from src.agents.guardrail import (
    GUARD_CALL_CONFIG,
    _latest_user_text,
    make_guardrail_node,
)
from src.schemas import GuardrailVerdict
from tests.fakes import make_guard

_SAP_OPS = GuardrailVerdict(allowed=True, category="sap_ops", reason="ok")
_OFF_TOPIC = GuardrailVerdict(allowed=False, category="off_topic", reason="unrelated")


async def test_allows_sap_ops_question() -> None:
    node = make_guardrail_node(make_guard(verdict=_SAP_OPS))
    out = await node({"messages": [HumanMessage(content="How is CPU on KHP?")]})
    # The verdict is written as a plain dict (checkpoint-serializable).
    assert out["guardrail"] == {"allowed": True, "category": "sap_ops", "reason": "ok"}


async def test_blocks_off_topic_question() -> None:
    node = make_guardrail_node(make_guard(verdict=_OFF_TOPIC))
    out = await node({"messages": [HumanMessage(content="Write me a poem about the sea.")]})
    assert out["guardrail"]["allowed"] is False


async def test_no_user_message_is_blocked_without_calling_model() -> None:
    # An AI-only message list must not reach the guard model and must not be allowed.
    calls = {"n": 0}

    def _boom(_messages: object) -> GuardrailVerdict:
        calls["n"] += 1
        raise AssertionError("guard model should not be called when there is no user turn")

    from langchain_core.runnables import RunnableLambda

    node = make_guardrail_node(RunnableLambda(_boom))
    out = await node({"messages": [AIMessage(content="earlier answer")]})
    assert out["guardrail"]["allowed"] is False
    assert out["guardrail"]["category"] == "off_topic"
    assert calls["n"] == 0


async def test_guard_error_fails_open() -> None:
    node = make_guardrail_node(make_guard(error=RuntimeError("openai down")))
    out = await node({"messages": [HumanMessage(content="How is memory on KBP?")]})
    # Fail-open: a transient guard failure allows the (trusted-operator) turn through, logged.
    assert out["guardrail"]["allowed"] is True
    assert out["guardrail"]["category"] == "sap_ops"
    assert "failing open" in out["guardrail"]["reason"].lower()


def test_latest_user_text_picks_most_recent_human() -> None:
    messages = [
        HumanMessage(content="first"),
        AIMessage(content="answer"),
        HumanMessage(content="second"),
    ]
    assert _latest_user_text(messages) == "second"


def test_latest_user_text_handles_multimodal_parts() -> None:
    msg = HumanMessage(content=[{"type": "text", "text": "hi"}, {"type": "image", "url": "x"}])
    assert _latest_user_text([msg]) == "hi"


def test_latest_user_text_none_when_no_human() -> None:
    assert _latest_user_text([AIMessage(content="only ai"), SystemMessage(content="sys")]) is None


def test_guard_call_config_suppresses_ui_emission() -> None:
    # This metadata is the contract with the AG-UI adapter that keeps guard output off the wire.
    assert GUARD_CALL_CONFIG["metadata"]["emit-messages"] is False
    assert GUARD_CALL_CONFIG["metadata"]["emit-tool-calls"] is False
