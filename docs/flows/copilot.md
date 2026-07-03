# Copilot flow

> **Status:** implemented (backend + frontend).
> **Endpoint:** `POST /copilot` (AG-UI SSE) · **Graph:** [`backend/src/flow/copilot.py`](../../backend/src/flow/copilot.py) · **Flow/agent name:** `copilot`
> **UI:** [`frontend/src/features/copilot/`](../../frontend/src/features/copilot/) · route `/copilot` (see [Frontend](#frontend-rasyona-ui))

The Copilot is the chatbot flow: an SAP Basis operator asks natural-language questions about the
~20 monitored SAP systems (health, performance, errors, availability, configuration status) and an
AI agent answers in **Markdown**, grounding every operational claim in live **Prometheus** metrics
and **Elasticsearch** logs. Flow progress (guardrail → tool calls → streamed answer) is pushed to
the UI in real time over the **AG-UI protocol** on a single SSE response.

---

## Capabilities

- **Grounded operational Q&A** — never invents metric values, thresholds, or log lines; if a tool
  returns no data it says so plainly.
- **Metric analysis** — per-category SAP metric summaries (min/max/avg/current/percentiles/trend),
  anomaly detection against catalog thresholds, app-server discovery, and advanced PromQL
  (instant/range/anomaly/baseline/correlation).
- **Log analysis** — field-scoped search, aggregations, window comparison, drill-down around an
  anchor event, error clustering, and a guarded raw-query escape hatch.
- **Natural-language metric discovery** — translates a phrase ("high CPU", "ABAP short dumps")
  into concrete Prometheus metric names before querying.
- **Per-conversation system scoping** — the answer is restricted to the systems the caller put in
  scope; unknown/injected ids are dropped.
- **Safety guardrail** — an up-front classifier blocks off-topic, unsafe, and prompt-injection
  turns with a deterministic refusal (no tools, no answering model).
- **Real-time streaming** — tool calls and Markdown tokens stream as they happen; the run always
  ends with a terminal event.
- **Tracing** — every turn is a Langfuse trace, grouped into a session by the client thread id.

---

## Graph topology

```
START → guardrail → route ─(allowed: sap_ops)→ agent  → END
                        └──(blocked)──────────→ refuse → END
```

Compiled once at app startup (pure — no network I/O) and reused per request. The graph is compiled
with an `InMemorySaver` checkpointer because the AG-UI adapter calls `graph.aget_state`; the
checkpoint is **per-request and ephemeral** (see [Security properties](#security-properties)).

### Nodes

| Node | Module | Responsibility |
|------|--------|----------------|
| `guardrail` | [`agents/guardrail.py`](../../backend/src/agents/guardrail.py) | Classify the latest user turn; write a `GuardrailVerdict` to state. |
| `agent` | [`agents/copilot_agent.py`](../../backend/src/agents/copilot_agent.py) | The answering agent (`create_agent`) bound to the SAP tool roster; streams the Markdown answer. |
| `refuse` | [`flow/copilot.py`](../../backend/src/flow/copilot.py) | Deterministic refusal `AIMessage` — no LLM, no tools — streamed to the UI. |

Routing (`_route`) sends the turn to `agent` only when the verdict is `allowed` (category
`sap_ops`); every other verdict routes to `refuse`.

### State

```python
class CopilotState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]  # conversation (reducer-merged)
    system_ids: list[str]                                # in-scope SAP systems for this turn
    guardrail: dict[str, Any] | None                     # verdict (plain dict, checkpoint-safe)
```

The public **output schema** (`CopilotOutputState`) intentionally exposes only `messages`. The
adapter derives its `STATE_SNAPSHOT` from the graph's *output* keys, so `guardrail` and
`system_ids` never leave the graph — the classifier's category and LLM-authored reason are not a
tuning oracle for the client.

---

## LLM models

Both LLM call sites use the **OpenAI** provider (`langchain-openai`, `ChatOpenAI`), constructed
through one shared builder, [`agents/_llm.py`](../../backend/src/agents/_llm.py), so the
provider-specific handling lives in a single place:

- **Answering agent** — capable model, streams free Markdown. **No `response_format`**: the
  structure-vs-streaming tension is resolved by keeping structure in *graph state* (the guardrail
  verdict) and letting the answer stream as prose.
- **Guardrail** — cheap/fast model with `with_structured_output(GuardrailVerdict,
  method="json_schema")` (OpenAI native strict structured outputs — a schema-validated verdict with
  no tool call). Runs at a low `reasoning_effort` — this is a snap classification, not a reasoning
  task. Structured output is fully supported on the GPT-5 family (`GuardrailVerdict` is a flat,
  all-required model with a `Literal` enum → a valid strict JSON schema).

Model ids and generation knobs are **non-secret config** in
[`backend/config/config.yaml`](../../backend/config/config.yaml) under `llm.*` (defaults live in
`LLMSettings` in [`backend/src/config.py`](../../backend/src/config.py)); read from config, never
hardcoded. The API key (`openai_api_key`) is a `SecretStr` sourced only from `.env`.

### Reasoning-model handling (GPT-5 / o-series)

The default models are **reasoning models** (`gpt-5.5` answerer, `gpt-5.4-mini` guard). These carry
API constraints the gpt-4o family didn't, handled as follows:

| Knob | Config key | Behaviour on a reasoning model |
|------|-----------|--------------------------------|
| `temperature` | `llm.temperature` | Only the model default is allowed; `langchain-openai` **drops** a custom value for `gpt-5*` (non-chat) / `o1`, so the configured `0.0` is honoured by gpt-4o but ignored by gpt-5.5 (no 400). |
| `max_tokens` | `llm.max_tokens` | Mapped to **`max_completion_tokens`**, a **combined reasoning + visible-output** budget — keep headroom (default `8192`) or long answers truncate (`finish_reason=length`). |
| `reasoning_effort` | `llm.answer_reasoning_effort` / `llm.guard_reasoning_effort` | `none`/`low`/`medium`/`high`/`xhigh`; **reasoning models only** — the builder forwards it **only** when the target model is a reasoning model (`is_reasoning_model`), so a swap to gpt-4o can't 400 on an unsupported param. Note the value must be valid for the *specific* reasoning generation (newer GPT-5 dropped `minimal` in favour of `none`); an unsupported value 400s and the guard **fails open**. `null` = model default. |

`gpt-5.5` / `gpt-5.4-mini` resolve to OpenAI's **Chat Completions API** (only the `*-pro` variants
force the Responses API), which is where `method="json_schema"` structured output and
`reasoning_effort` apply. Reasoning tokens are **not** emitted as content, so no chain-of-thought
reaches the UI — only the streamed Markdown answer.

---

## Tool roster

The answering agent binds exactly the curated list from
[`get_all_tools()`](../../backend/src/tools/__init__.py). Tools obey the **credential firewall**:
each imports only the non-secret YAML loaders and the shared clients, never `get_settings()`, so a
tool can never touch a secret.

| Tool | Source | What it does |
|------|--------|--------------|
| `tool_sap_metric_categorized` | [`sap_metric_categorized/`](../../backend/src/tools/sap_metric_categorized/) | Expand a SAP metric **category/profile** to its catalog metrics, query Prometheus, summarize (min/max/avg/current/p50/p90/p95/p99/trend), flag anomalies vs `alert_threshold`, and discover app servers. |
| `metric_lookup` | [`metric_lookup/`](../../backend/src/tools/metric_lookup/) | Translate a natural-language phrase into ranked catalog metrics (`prometheus_names`). Deterministic catalog resolver by default; optional semantic/embedding resolver behind a config flag (same `MetricLookupResolver` seam). |
| `prometheus_metrics_advance_query` | [`prometheus_advanced_query/`](../../backend/src/tools/prometheus_advanced_query/) | Advanced PromQL engine: `instant` / `range` / `anomaly_check` (z-score) / `baseline_compare` / `correlation` (pure-python Pearson). |
| `list_sap_systems` | [`systems.py`](../../backend/src/tools/systems.py) | List managed systems — **`name` / `display_name` / `environment` only**. Never exposes host / user / password / sysnr / client. |
| `es_field_search` | [`elasticsearch/`](../../backend/src/tools/elasticsearch/) | Field-scoped log search (time range + `system_id` term + governed projection). Both `must_match` filters **and** `fields_to_return` projections are validated against the profile's searchable fields; unknown names return `invalid_request` before any ES query. |
| `es_aggregation` | [`elasticsearch/`](../../backend/src/tools/elasticsearch/) | Terms / date-histogram / count / cardinality aggregations over logs. The aggregation `field` (whose distinct values a `terms` agg streams back) and `filter_must` are both validated against the profile; unknown names return `invalid_request` before any ES query. |
| `es_compare_windows` | [`elasticsearch/`](../../backend/src/tools/elasticsearch/) | Compare two time windows; surfaces signatures new in the later window. |
| `es_drilldown_around` | [`elasticsearch/`](../../backend/src/tools/elasticsearch/) | Fetch context before/after an anchor event (`delta_ms`, `anchor_not_found`). |
| `es_cluster_errors` | [`elasticsearch/`](../../backend/src/tools/elasticsearch/) | Normalize + cluster errors into ranked clusters (deterministic signatures/ids). |
| `es_raw_query` | [`elasticsearch/`](../../backend/src/tools/elasticsearch/) | Raw ES query behind a fail-fast safety envelope (script / bool-nesting / endpoint allowlist / timeout / size cap / high-volume opt-in). |

**Cross-cutting tool invariants** (see project memory / backend CLAUDE.md):

- Every Elasticsearch query is scoped by `{"term": {"system_id": <ID>.upper()}}` — one shared index
  serves all systems, so `system_id` is a filter value, never a routing key.
- Tool results are relayed to the browser via `TOOL_CALL_RESULT`, so returns must be **JSON-safe**
  (no `NaN`/`Inf`) and **leak-free** (generic error strings; the real exception is logged
  server-side). Field-selecting inputs (`es_field_search`'s `must_match` / `fields_to_return`,
  `es_aggregation`'s `filter_must` / `field`) are validated against the resolved profile's searchable
  fields so an ungoverned field can't be exfiltrated through the browser-visible result — a `terms`
  aggregation in particular streams back the distinct *values* of its `field`.
- Multi-app-server Prometheus results are aggregated **order-independently** (deterministic).
- Log/metric responses are governed by a 256 KB byte cap.

---

## Scope resolution

`resolve_system_ids()` ([`agui/runner.py`](../../backend/src/agui/runner.py)) decides the in-scope
systems for each turn:

1. An AG-UI `context` entry whose `description` is one of `system_ids` / `systems` / `system` /
   `scope` (comma-separated value), else
2. a pre-populated `state["system_ids"]`, else
3. **all** managed systems.

Every candidate id is validated against `get_systems()` **case-insensitively** (stripped +
upper-cased to the catalog's canonical casing, matching the `system_id.upper()` ES filters); unknown
ids are **dropped** (logged). Case-insensitive matching matters for safety, not just ergonomics: a
mistyped `khp` must resolve to `KHP` rather than being dropped and silently widening scope to *all*
systems. This is a security boundary: untrusted `context`/`state` cannot smuggle arbitrary text into
the answering agent's trusted system prompt via the scope channel. The base prompt lives in
[`backend/src/prompts/copilot.py`](../../backend/src/prompts/copilot.py). The resolved list is
injected into graph state, reaches the agent subgraph's `CopilotAgentState`, and a
`dynamic_prompt` middleware appends the scope line to the committed base prompt at invoke time.

---

## AG-UI SSE contract

`POST /copilot` accepts an AG-UI `RunAgentInput` (camelCase on the wire: `threadId`, `runId`,
`messages`, `context`, ...) and returns `text/event-stream`. The driver is
[`run_copilot_stream`](../../backend/src/agui/runner.py); it reuses `ag_ui_langgraph.LangGraphAgent`
as the event-mapping engine but keeps its own thin driver so it owns request scoping, Langfuse
tracing, and a guaranteed terminal event.

**Happy path (a SAP-ops question with a tool call):**

```
RUN_STARTED
  TOOL_CALL_START → TOOL_CALL_ARGS* → TOOL_CALL_END → TOOL_CALL_RESULT   (per tool call)
  TEXT_MESSAGE_START → TEXT_MESSAGE_CONTENT* → TEXT_MESSAGE_END          (streamed Markdown)
RUN_FINISHED
```

**Refusal path (blocked turn):** `RUN_STARTED → TEXT_MESSAGE_* (refusal text) → RUN_FINISHED` —
**no `TOOL_CALL_*`**, and the answering model is never invoked.

**Failure:** the terminal frame is `RUN_ERROR` with a **generic** message; the real exception is
logged server-side (may carry internal hostnames / upstream bodies) and never sent to the browser.

SSE headers disable caching + proxy buffering (`Cache-Control: no-cache`, `X-Accel-Buffering: no`)
so frames flush immediately.

---

## Frontend (Rasyona UI)

The `/copilot` page is a streaming chat in the **Rasyona** app shell (Vite + React + TypeScript,
Ant Design v5, dark-first). It consumes this endpoint directly via the AG-UI TS SDK
(`@ag-ui/client`, exact-pinned) — no CopilotKit; the chat UI is custom. The **route path mirrors the
endpoint path** and the flow name matches on both sides.

- **App shell** — [`frontend/src/app/`](../../frontend/src/app/): header wordmark "Rasyona", a
  left menu driven by a single flows registry ([`app/flows.tsx`](../../frontend/src/app/flows.tsx))
  with Copilot first and the other flows shown as a "Coming soon" roadmap. `/` redirects to
  `/copilot`. Theme tokens live in [`app/theme.ts`](../../frontend/src/app/theme.ts).
- **SDK seam** — components never import the SDK directly; [`lib/agui.ts`](../../frontend/src/lib/agui.ts)
  exposes `createFlowAgent('/copilot')` (an `HttpAgent`) and re-exports the SDK types. Backend bases
  come only from [`lib/env.ts`](../../frontend/src/lib/env.ts) (`VITE_AGUI_BASE_URL`, `VITE_API_URL`).
- **State machine** — [`hooks/useCopilotAgent.ts`](../../frontend/src/features/copilot/hooks/useCopilotAgent.ts)
  subscribes to the agent and maps the AG-UI event families to view state. Contract details it
  relies on: **stateless replay** (the full message history is POSTed each turn; `threadId` only
  groups traces); a guardrail refusal is a **normal** run (no error UI); `RUN_ERROR` is terminal and
  may arrive mid-message (the open message is flagged *interrupted*); a `503` maps to a
  "service not configured" message; a user **Stop** aborts the run and flags the message partial
  (no error banner). Emissions from a superseded agent (after **New conversation**) are fenced out
  so they can't corrupt the fresh conversation.
- **Tool timeline** — the signature element. Each `TOOL_CALL_*` renders as an inspectable step
  (friendly label + spinner → check) whose body shows the exact args/result JSON, so an operator can
  audit what the agent did. Tool names map to labels in
  [`features/copilot/types.ts`](../../frontend/src/features/copilot/types.ts).
- **Markdown** — assistant answers render through
  [`components/MarkdownContent.tsx`](../../frontend/src/components/MarkdownContent.tsx) with GFM
  (tables/code). **No `rehype-raw`**, so any raw HTML in a model answer is inert text (XSS-safe);
  links open in a new tab with `noopener`.
- **Dev transport** — Vite proxies `/agui/*` → backend `/*` (SSE-safe: `accept-encoding: identity`,
  no proxy timeout) and `/api/*` → backend `/*`. The `/agui` prefix keeps SPA route GETs (a refresh
  on `/copilot`) out of the proxy. In production, set absolute `VITE_*` URLs **and** add the origin to
  `cors.allow_origins`, or replicate the rewrites in a reverse proxy.
- **Tests** — Vitest + React Testing Library + MSW-mocked SSE (48 tests). The wire fixtures in
  `frontend/src/test/aguiEvents.ts` mirror the sequences in
  [`tests/agui/test_copilot_endpoint_sse.py`](../../backend/tests/agui/test_copilot_endpoint_sse.py).

### Live verification checklist (needs the backend running with an LLM key)

Run `uv run uvicorn src.main:app --reload --port 8000` (from `backend/`) and `npm run dev` (from
`frontend/`), then at `http://localhost:5173/copilot`:

1. "Which SAP systems do you monitor?" → a **Listing SAP systems** tool step pulses, then a streamed
   answer; expand the step to see args/result JSON.
2. A metric question (e.g. "Any high CPU on KHP in the last hour?") → Prometheus tool step(s) → a
   Markdown table renders.
3. An off-topic question → the guardrail refusal streams as a normal message (no error UI).
4. **Stop** mid-answer → the message shows an *interrupted* tag, no error banner. **New conversation**
   → empty state returns.
5. Backend stopped → send → a transport-error alert. Backend without the LLM key → the
   "service not configured" message.
6. DevTools ▸ Network: a single POST to `/agui/copilot`, events arriving **incrementally** (not all at
   once at the end — that would indicate proxy buffering).

---

## Security properties

- **Credential firewall** — tools never receive secrets; only client builders + `main.py` read
  `get_settings()`. `openai_api_key`/tokens/hosts are `SecretStr` sourced from `.env` only.
- **No secret exposure** — `list_sap_systems` and every state snapshot expose only
  `name`/`display_name`/`environment`; committed `systems.yaml` passwords are dropped at the loader
  boundary and never enter memory.
- **Guardrail verdict is never leaked** — excluded from the output schema, so its category/reason
  never reach the wire.
- **`raw_event` scrubbing** — the adapter's pass-through `RAW` events (which wrap node
  inputs/outputs, incl. the guard's system prompt + tool inputs) are dropped; `raw_event` is
  stripped from every other event so only semantic AG-UI fields reach the client.
- **Per-request ephemeral checkpoint** — the client `thread_id` is used only for Langfuse session
  grouping; each turn runs under a fresh server-generated checkpoint id that is deleted in `finally`,
  so two callers sharing a `thread_id` can't read each other's conversation and memory is bounded.
- **Fail-open guardrail** — if the guard model errors, the turn is allowed through (logged). The
  blast radius is bounded: the answering agent has only read-only metric/log tools and the firewall
  keeps secrets out.
- **Guaranteed terminal event** — all setup that can raise runs inside the try, so no failure yields
  an empty 200 body that hangs the client.

---

## Configuration

Non-secret ([`backend/config/config.yaml`](../../backend/config/config.yaml)):

- `llm.answer_model`, `llm.guard_model`, `llm.temperature`, `llm.max_tokens`
- `copilot.max_tool_iterations`
- `cors.allow_origins` (Vite dev origin), `elasticsearch.index_name`

Secret (`.env` only — see [`backend/.env.example`](../../backend/.env.example)):

- `OPENAI_API_KEY` (required — without it the graph is not compiled and `POST /copilot` returns
  **503**)
- `PROMETHEUS_URL` / `PROMETHEUS_TOKEN`, `ELASTICSEARCH_HOSTS` / `ELASTICSEARCH_API_KEY`
- `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_BASE_URL` (tracing; optional)

Env overrides YAML; nested keys use `SECTION__KEY` (e.g. `LLM__ANSWER_MODEL`).

---

## Failure modes

| Condition | Behaviour |
|-----------|-----------|
| `OPENAI_API_KEY` absent | Graph not compiled; `POST /copilot` → **503** (`copilot_disabled` logged). |
| Guard model error | **Fail-open**: turn allowed as `sap_ops` (logged). |
| Tool / node error mid-stream | Terminal **`RUN_ERROR`** with a generic message; real error logged. |
| Prometheus/ES unavailable | Tool returns a leak-free error; the agent reports "no data" rather than inventing values. |

---

## Tracing

Langfuse v4: `CallbackHandler` is attached to the graph run and the turn is wrapped in
`propagate_attributes(trace_name="copilot", session_id=<client threadId>)`. `langfuse.flush()`
runs per turn (in `finally`) and on shutdown so async delivery doesn't drop traces. Tracing is
best-effort — a Langfuse failure never breaks a turn.

---

## Tests

- [`tests/agents/test_guardrail.py`](../../backend/tests/agents/test_guardrail.py) — classification
  wiring, message extraction, fail-open (guard model faked; hermetic).
- [`tests/flow/`](../../backend/tests/flow/) — graph routing (off-topic → refuse without invoking
  the agent model; sap-ops → agent + tool call).
- [`tests/agui/test_copilot_endpoint_sse.py`](../../backend/tests/agui/test_copilot_endpoint_sse.py)
  — the on-the-wire event sequence, camelCase fields, refusal path, guaranteed terminal `RUN_ERROR`,
  scope resolution, and the 503-without-key path. The LLM is scripted; tools are mocked at the
  client boundary; Langfuse is a no-op.

Manual smoke test against a live server + real model:
[`scripts/smoke_copilot.py`](../../backend/scripts/smoke_copilot.py).

---

## Source map

| Concern | Path |
|---------|------|
| Graph wiring | [`backend/src/flow/copilot.py`](../../backend/src/flow/copilot.py) |
| Answering agent | [`backend/src/agents/copilot_agent.py`](../../backend/src/agents/copilot_agent.py) |
| Answering prompt | [`backend/src/prompts/copilot.py`](../../backend/src/prompts/copilot.py) |
| Guardrail | [`backend/src/agents/guardrail.py`](../../backend/src/agents/guardrail.py) |
| AG-UI SSE driver | [`backend/src/agui/runner.py`](../../backend/src/agui/runner.py) |
| Endpoint (`POST /copilot`) | [`backend/src/main.py`](../../backend/src/main.py) |
| Tool roster | [`backend/src/tools/__init__.py`](../../backend/src/tools/__init__.py) |
| Shared schemas (`GuardrailVerdict`) | [`backend/src/schemas.py`](../../backend/src/schemas.py) |
| Config | [`backend/src/config.py`](../../backend/src/config.py) · [`backend/config/config.yaml`](../../backend/config/config.yaml) |
