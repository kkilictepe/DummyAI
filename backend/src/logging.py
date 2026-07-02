"""Structured logging (structlog) + a per-request ``request_id`` bound via contextvars.

The request-id middleware is a **pure ASGI** middleware on purpose: Starlette's
``BaseHTTPMiddleware`` buffers streaming responses, which would break the AG-UI SSE stream.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any, cast

import structlog

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

    from src.config import Settings


def setup_logging(settings: Settings) -> None:
    """Configure structlog: JSON in production, human console otherwise."""
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]
    renderer: Any = (
        structlog.processors.JSONRenderer()
        if settings.environment == "production"
        else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a structlog logger."""
    return cast("structlog.stdlib.BoundLogger", structlog.get_logger(name))


def bind_request_id(request_id: str) -> None:
    """Bind ``request_id`` (and clear prior context) for the current async context."""
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=request_id)


def get_request_id() -> str | None:
    """Return the ``request_id`` bound for the current async context, if any."""
    value = structlog.contextvars.get_contextvars().get("request_id")
    return value if isinstance(value, str) else None


class RequestIdMiddleware:
    """Pure-ASGI middleware: assign/propagate ``x-request-id`` and bind it to log context."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        raw = headers.get(b"x-request-id", b"")
        request_id = raw.decode("latin-1") if raw else str(uuid.uuid4())
        bind_request_id(request_id)

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                message["headers"].append((b"x-request-id", request_id.encode("latin-1")))
            await send(message)

        await self.app(scope, receive, send_wrapper)
