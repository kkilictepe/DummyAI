"""AG-UI transport glue: adapts the compiled Copilot LangGraph graph to an AG-UI SSE event stream.

This package is intentionally outside CLAUDE.md's ``clients``/``tools``/``agents``/``flow`` layout:
it is transport-only (neither a client nor a flow), owning the ``LangGraphAgent`` → SSE mapping,
per-request isolation, Langfuse tracing, and the guaranteed terminal event.
"""
