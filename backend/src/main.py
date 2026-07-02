"""FastAPI application factory.

- ``create_app()`` builds the app (logging, CORS, ``/health``) and performs **no** network I/O.
- The **lifespan** builds the two shared transport clients (Prometheus, Elasticsearch), registers
  them via ``src.clients.set_clients`` so tool nodes can reach them, initialises Langfuse
  (best-effort), and tears everything down on shutdown.

Later phases add the compiled Copilot graph and the ``/copilot`` AG-UI SSE endpoint.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from ag_ui.core import RunAgentInput
from fastapi import FastAPI, HTTPException, Request
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import StreamingResponse

from src.agui.runner import run_copilot_stream
from src.clients import (
    build_es_client,
    build_prometheus_client,
    close_clients,
    get_es_client,
    get_prometheus_client,
    set_clients,
)
from src.config import get_settings, validate_config
from src.flow.copilot import build_copilot_graph
from src.logging import RequestIdMiddleware, get_logger, get_request_id, setup_logging
from src.schemas import HealthResponse

if TYPE_CHECKING:
    from src.config import Settings

# SSE headers: disable caching and proxy buffering so frames flush immediately (an intermediary
# that buffers the response makes the stream appear to "hang" on the client).
_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}

_HEALTHY_ES_STATUSES = {"green", "yellow"}


def _init_langfuse(settings: Settings, log: Any) -> Any:
    """Initialise the Langfuse client (best-effort, non-fatal).

    Bridges validated settings into ``os.environ`` (without clobbering an existing process env)
    so both the SDK client and the Phase 3 ``CallbackHandler`` — which read ``os.environ`` —
    observe the same credentials sourced from ``.env``/config. Returns ``None`` when disabled or
    unreachable so startup never fails on tracing.
    """
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        log.info("langfuse_disabled", reason="missing_keys")
        return None

    os.environ.setdefault("LANGFUSE_PUBLIC_KEY", settings.langfuse_public_key.get_secret_value())
    os.environ.setdefault("LANGFUSE_SECRET_KEY", settings.langfuse_secret_key.get_secret_value())
    os.environ.setdefault("LANGFUSE_HOST", settings.langfuse_base_url)
    try:
        from langfuse import get_client

        client = get_client()
        auth_ok = client.auth_check()
        log.info("langfuse_ready", auth_ok=auth_ok)
        return client
    except Exception as exc:
        log.warning("langfuse_init_failed", error=str(exc))
        return None


def create_app() -> FastAPI:
    """Build and configure the FastAPI app. Pure — performs no network I/O."""
    settings = get_settings()
    setup_logging(settings)
    log = get_logger(__name__)

    for warning in validate_config():
        log.warning("config_validation", issue=warning)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        prometheus = build_prometheus_client(settings)
        try:
            es = build_es_client(settings)
        except Exception:
            # The Prometheus client already opened an httpx pool; don't leak it if ES fails.
            await prometheus.aclose()
            raise
        set_clients(prometheus, es)
        app.state.prometheus = prometheus
        app.state.es = es
        app.state.langfuse = _init_langfuse(settings, log)
        log.info("clients_initialised", prometheus_url=settings.prometheus_url)
        try:
            yield
        finally:
            await close_clients()
            langfuse = getattr(app.state, "langfuse", None)
            if langfuse is not None:
                try:
                    langfuse.flush()
                except Exception as exc:
                    log.warning("langfuse_flush_failed", error=str(exc))
            log.info("clients_closed")

    app = FastAPI(title="Dummy AI Backend", version="0.1.0", lifespan=lifespan)

    # Compile the Copilot graph once here (pure — no network I/O; clients are only touched at
    # tool-invoke time via the module accessors the lifespan sets). Requires the OpenAI key to
    # construct the models, so we skip it (and /copilot returns 503) when the key is absent.
    app.state.copilot_graph = None
    if settings.openai_api_key is not None:
        try:
            app.state.copilot_graph = build_copilot_graph(settings)
            log.info("copilot_graph_compiled")
        except Exception as exc:
            log.error("copilot_graph_build_failed", error=str(exc))
    else:
        log.warning("copilot_disabled", reason="missing_openai_api_key")

    # Request-id first (inner), CORS added last so it wraps outermost.
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors.allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health", response_model=HealthResponse)
    async def health(deep: bool = False) -> HealthResponse:
        if not deep:
            return HealthResponse(status="ok", environment=settings.environment)

        deps: dict[str, str] = {}

        try:
            resp = await get_prometheus_client().instant_query("up")
            deps["prometheus"] = "ok" if resp.success else "degraded"
        except Exception as exc:
            deps["prometheus"] = "unavailable"
            log.warning("health_prometheus_failed", error=str(exc))

        try:
            cluster = await get_es_client().health()
            reachable = str(cluster.get("status")) in _HEALTHY_ES_STATUSES
            deps["elasticsearch"] = "ok" if reachable else "degraded"
        except Exception as exc:
            deps["elasticsearch"] = "unavailable"
            log.warning("health_elasticsearch_failed", error=str(exc))

        if all(state == "ok" for state in deps.values()):
            return HealthResponse(status="ok", environment=settings.environment, dependencies=deps)
        return HealthResponse(
            status="degraded", environment=settings.environment, dependencies=deps
        )

    @app.post("/copilot")
    async def copilot(run_input: RunAgentInput, request: Request) -> StreamingResponse:
        """AG-UI SSE endpoint for the Copilot flow. Path mirrors the future ``/copilot`` route."""
        graph = getattr(request.app.state, "copilot_graph", None)
        if graph is None:
            raise HTTPException(
                status_code=503,
                detail="Copilot is not configured (missing OPENAI_API_KEY).",
            )
        request_id = get_request_id() or str(uuid4())
        return StreamingResponse(
            run_copilot_stream(
                graph,
                run_input,
                request_id=request_id,
                langfuse_client=getattr(request.app.state, "langfuse", None),
            ),
            media_type="text/event-stream",
            headers=_SSE_HEADERS,
        )

    log.info("app_created", environment=settings.environment)
    return app


app = create_app()
