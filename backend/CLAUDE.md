# Backend — Dummy AI (FastAPI + LangGraph agents)

FastAPI REST API hosting AI-agent flows. Python **3.12**, managed with **uv**. Agents use
LangChain/LangGraph (+ **deepagents** for complex orchestration). Tracing via **Langfuse**.

## Commands (run from `backend/`)
```bash
uv sync                                            # install from uv.lock (creates .venv)
uv add <pkg>            # uv add --dev <pkg>        # add runtime / dev dependency
uv run uvicorn src.main:app --reload --port 8000   # dev server
uv run pytest -q                                   # tests (single: uv run pytest tests/test_x.py::test_y)
uv run ruff check . && uv run ruff format .        # lint + format
uv run mypy src                                    # type-check
```
- **Always use uv. NEVER call bare `pip` / `python`** — deps must be tracked in `uv.lock`.
- Commit `pyproject.toml` + `uv.lock`; never commit `.venv/` or `.env`.

## Layout
- `config/`        — committed **YAML** (`config.yaml`, optional `config.<env>.yaml`). **Non-secret only.**
- `src/main.py`    — app factory: logging setup, lifespan (client startup/shutdown + `langfuse.flush()`),
  CORS for the Vite origin, registers one AG-UI endpoint per flow.
- `src/config.py`  — `Settings(BaseSettings)` (pydantic-settings); layers YAML + `.env` so env/`.env`
  override YAML. Expose `get_settings()` (`@lru_cache`). Read config only through this.
- `src/logging.py` — **structlog** (JSON in prod, console in dev; request_id via contextvars).
- `src/clients/`   — transport wrappers; one shared async instance each.
- `src/tools/`     — LangChain `@tool`s; thin, call clients.
- `src/agents/`    — agent factories returning compiled agents.
- `src/flow/`      — LangGraph `StateGraph` orchestration; one flow per module.
- `src/schemas.py` — shared Pydantic models (tool args + agent structured outputs).
- `tests/`         — pytest, mirrors `src/`.

## Agents (LangChain 1.x / LangGraph 1.x)
- Build agents with `from langchain.agents import create_agent`.
  **Do NOT use the legacy `langgraph.prebuilt.create_react_agent`.**
- Use `deepagents.create_deep_agent` only for complex multi-step / orchestrating agents.
- **Agents MUST return structured output**: define a Pydantic `BaseModel`, pass `response_format=Model`
  to `create_agent`, read `result["structured_response"]`. Prefer
  `ToolStrategy(schema=Model, handle_errors=True)` (from `langchain.agents.structured_output`) for
  cross-model retries. (Gotcha: Anthropic extended-thinking + structured output can conflict.)
- Every tool: `@tool` + a Pydantic `args_schema` with `Field(description=...)`. Agents import curated
  tool lists; **tools never receive credentials**.
- Raw graph wiring lives only in `flow/`. Compiled agents are embedded as nodes there.

## Clients — Prometheus + Elasticsearch
- **Async-first**: clients and tools are `async def`; wire lifecycle into the FastAPI lifespan.
- Prometheus: `PrometheusClient` over one shared `httpx.AsyncClient` hitting `/api/v1/query` and
  `/api/v1/query_range` (POST for large queries). Values come back as **strings — float-cast them**;
  instant = vector, range = matrix. (Do not use sync `prometheus-api-client` on the event loop.)
- Elasticsearch: one shared `AsyncElasticsearch` (`elasticsearch[async]`, pin **`>=9,<10`** — a 9.x
  client needs an ES9 server). Query builder import is `from elasticsearch.dsl import Search, Q`.
  **Call `await client.close()` on shutdown.** Never create a client per request.
- Constrain tool inputs with Pydantic (service, level, text, time window, capped limit/step); build the
  actual PromQL/ES query server-side. Return compact, token-efficient results.

## Config & secrets
- YAML (`config/`) = non-secret structure. `.env` (gitignored) = secrets: `PROMETHEUS_URL`,
  `PROMETHEUS_TOKEN`, `ELASTICSEARCH_HOSTS`, `ELASTICSEARCH_API_KEY`, `LANGFUSE_PUBLIC_KEY`,
  `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL`, and the LLM provider key. Nested overrides use `SECTION__KEY`.
- Copy `.env.example` → `.env`. **Never commit `.env`.**

## Tracing — Langfuse v4
- `from langfuse import get_client, propagate_attributes` and
  `from langfuse.langchain import CallbackHandler`. **Not** the legacy `from langfuse.callback import ...`.
- `handler = CallbackHandler()` (reads keys from env). Pass `config={"callbacks": [handler]}` to every
  graph/agent `.invoke` / `.stream`. Wrap a turn in
  `with propagate_attributes(trace_name=..., session_id=..., user_id=...):` for session grouping.
- `get_client().auth_check()` at startup; **`langfuse.flush()` on shutdown** (async delivery drops traces otherwise).

## AG-UI endpoint
- One SSE POST endpoint per flow. **Always terminate the stream with `RUN_FINISHED` / `RUN_ERROR`.**
  Enable CORS for the Vite dev origin (`http://localhost:5173`).

## Testing (required after every change)
- `pytest` + `pytest-asyncio` (`asyncio_mode = "auto"` in `pyproject.toml`) + httpx
  `AsyncClient(transport=ASGITransport(app=app))`.
- Mock at the client boundary: Prometheus via `respx` / `httpx.MockTransport`; stub
  `ElasticsearchClient.search_logs`.
