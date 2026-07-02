"""Drive the Copilot graph and encode its output as an AG-UI SSE byte stream.

We reuse ``ag_ui_langgraph.LangGraphAgent`` as the event-mapping engine (it maps content-block
chunks → TEXT_MESSAGE_*, tool-call chunks → TOOL_CALL_*, emits STATE/MESSAGES snapshots, and
generates stable ids) but keep our own thin driver instead of ``add_langgraph_fastapi_endpoint``
so we own request scoping, Langfuse tracing, and a **guaranteed terminal event**.

Two isolation/safety properties this driver enforces:

* **Per-request ephemeral checkpoint.** ``LangGraphAgent`` requires a checkpointer (it calls
  ``aget_state``) and keys it on ``RunAgentInput.thread_id``. Since the graph + its shared
  ``InMemorySaver`` are process-wide, trusting the client-supplied ``thread_id`` verbatim lets two
  callers that present the same id read/append each other's conversation. Our design is stateless
  replay — the client sends full history every turn — so we don't need cross-turn server
  persistence: we run each turn under a fresh server-generated checkpoint id and delete it
  afterwards (bounding memory). The client's ``thread_id`` is kept only for Langfuse session
  grouping.
* **Guaranteed terminal event.** All setup that can raise runs *inside* the try, so any failure
  (scope resolution, adapter construction, mid-stream error) is surfaced as a terminal RUN_ERROR
  rather than an empty 200 body that hangs the client.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

from ag_ui.core import EventType, RunAgentInput, RunErrorEvent
from ag_ui.encoder import EventEncoder
from ag_ui_langgraph import LangGraphAgent

from src.config import get_systems
from src.logging import get_logger

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

_log = get_logger(__name__)

# RunAgentInput.context entries whose description marks the in-scope system list.
_SCOPE_KEYS = frozenset({"system_ids", "systems", "system", "scope"})

# Shown to the client on any unhandled run failure. The real exception (which may carry internal
# hostnames / upstream error bodies) is logged server-side, never sent to the browser.
_GENERIC_ERROR = "The copilot hit an internal error while processing this request."


def resolve_system_ids(run_input: RunAgentInput) -> list[str]:
    """Determine the in-scope SAP system ids for this run.

    Scope must be supplied on **every** turn (this driver replays full history but keeps no
    server-side scope state): an explicit ``context`` scope entry (comma-separated) or a
    pre-populated ``state["system_ids"]``; absent either, we default to all managed systems.

    Every candidate id is validated against the managed-systems catalog (``get_systems()``) and
    unknown ids are dropped — so untrusted ``context``/``state`` cannot smuggle arbitrary text into
    the answering agent's (trusted) system prompt via the scope channel. Matching is
    **case-insensitive** (ids are stripped + upper-cased, the codebase's canonical casing — see the
    ``system_id.upper()`` ES filters), so a client asking about ``khp`` is scoped to ``KHP`` rather
    than being silently dropped and widening scope to *all* systems. If nothing valid remains, we
    fall back to all managed systems. Order-preserving, de-duplicated, and returned in the catalog's
    canonical casing.
    """
    managed = list(get_systems().keys())
    # Upper-cased key -> catalog's canonical id, so matching tolerates client casing while the
    # returned ids stay exactly as the managed-systems catalog spells them.
    canonical = {system_id.upper(): system_id for system_id in managed}

    raw: str | None = None
    for ctx in run_input.context or []:
        description = getattr(ctx, "description", None)
        if isinstance(description, str) and description.strip().lower() in _SCOPE_KEYS:
            raw = getattr(ctx, "value", None)
            break

    requested: list[str]
    if raw:
        requested = [part.strip() for part in raw.split(",") if part.strip()]
    elif isinstance(run_input.state, dict) and run_input.state.get("system_ids"):
        requested = [str(s) for s in run_input.state["system_ids"]]
    else:
        requested = managed

    seen: set[str] = set()
    deduped: list[str] = []
    dropped: list[str] = []
    for system_id in requested:
        canonical_id = canonical.get(str(system_id).strip().upper())
        if canonical_id is None:
            dropped.append(system_id)
            continue
        if canonical_id not in seen:
            seen.add(canonical_id)
            deduped.append(canonical_id)

    if dropped:
        _log.warning("scope_ids_dropped", dropped=dropped)
    return deduped or managed


def _prepare_run_input(
    run_input: RunAgentInput, system_ids: list[str], checkpoint_thread_id: str
) -> RunAgentInput:
    """Inject validated ``system_ids`` into initial state (AG-UI delivers state to graph state) and
    swap ``thread_id`` for a fresh per-request checkpoint id (isolation — see module docstring)."""
    state = dict(run_input.state) if isinstance(run_input.state, dict) else {}
    state["system_ids"] = system_ids
    return run_input.model_copy(update={"state": state, "thread_id": checkpoint_thread_id})


def _trace_context(session_id: str | None, langfuse_client: Any) -> Any:
    """Langfuse session-grouping context, or a no-op when tracing is disabled."""
    if langfuse_client is None:
        return contextlib.nullcontext()
    from langfuse import propagate_attributes

    return propagate_attributes(trace_name="copilot", session_id=session_id)


async def run_copilot_stream(
    graph: CompiledStateGraph[Any, Any, Any, Any],
    run_input: RunAgentInput,
    *,
    request_id: str,
    langfuse_client: Any = None,
) -> AsyncGenerator[str, None]:
    """Yield AG-UI SSE frames for one Copilot turn. Never raises; always ends with a terminal
    event (RUN_FINISHED from the adapter on success, or a RUN_ERROR we emit on failure).

    Note: a failure that occurs *after* a TEXT_MESSAGE/TOOL_CALL block has opened emits RUN_ERROR
    without a matching END for that block. RUN_ERROR is a valid run-level terminator (the client
    never hangs); strict per-message pairing on abort is a known, low-impact limitation.
    """
    encoder = EventEncoder()
    session_id = run_input.thread_id  # client conversation id -> Langfuse session grouping only
    checkpoint_thread_id = uuid.uuid4().hex  # ephemeral, per-request; deleted in finally

    try:
        callbacks: list[Any] = []
        if langfuse_client is not None:
            try:
                from langfuse.langchain import CallbackHandler

                callbacks = [CallbackHandler()]
            except Exception as exc:  # tracing is best-effort, never fatal
                _log.warning("langfuse_callback_unavailable", error=str(exc))

        system_ids = resolve_system_ids(run_input)
        run_input = _prepare_run_input(run_input, system_ids, checkpoint_thread_id)
        _log.info("copilot_run_start", request_id=request_id, system_ids=system_ids)

        agent = LangGraphAgent(name="copilot", graph=graph, config={"callbacks": callbacks}).clone()
        with _trace_context(session_id, langfuse_client):
            async for event in agent.run(run_input):
                # The adapter passes through internal LangGraph state that the client neither needs
                # nor should see: RAW events wrap whole astream payloads, and every other event
                # carries a ``raw_event`` with the node's inputs/outputs (which include the
                # guardrail's system prompt + verdict and tool inputs). Drop RAW entirely and strip
                # ``raw_event`` from the rest so only the semantic AG-UI fields reach the client.
                if event.type == EventType.RAW:
                    continue
                if getattr(event, "raw_event", None) is not None:
                    event.raw_event = None
                yield encoder.encode(event)
    except Exception:  # must surface as a terminal RUN_ERROR, never a 500 / hung client
        _log.exception("copilot_run_failed", request_id=request_id)
        yield encoder.encode(RunErrorEvent(type=EventType.RUN_ERROR, message=_GENERIC_ERROR))
    finally:
        # Drop the ephemeral checkpoint so per-request threads don't accumulate in memory.
        checkpointer = getattr(graph, "checkpointer", None)
        if checkpointer is not None:
            try:
                await checkpointer.adelete_thread(checkpoint_thread_id)
            except Exception as exc:  # best-effort cleanup
                _log.warning("checkpoint_cleanup_failed", error=str(exc))
        if langfuse_client is not None:
            try:
                langfuse_client.flush()
            except Exception as exc:  # best-effort flush
                _log.warning("langfuse_flush_failed", error=str(exc))
