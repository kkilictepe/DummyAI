"""Shared Pydantic models: API responses, agent structured outputs, and base tool-arg types."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    """Response for ``GET /health``."""

    status: Literal["ok", "degraded"] = "ok"
    environment: str
    # Populated only for ``?deep=1`` once clients exist (Phase 1).
    dependencies: dict[str, str] | None = None


# ---------------------------------------------------------------------------
# Guardrail (Copilot flow, Phase 3) — structured output the guardrail agent returns
# ---------------------------------------------------------------------------

GuardrailCategory = Literal["sap_ops", "off_topic", "unsafe", "prompt_injection"]


class GuardrailVerdict(BaseModel):
    """Classification of the latest user message by the Copilot guardrail."""

    allowed: bool = Field(description="True only when the request is safe, in-scope SAP ops.")
    category: GuardrailCategory = Field(description="Why the request was allowed or blocked.")
    reason: str = Field(description="Short human-readable justification.")


# ---------------------------------------------------------------------------
# Base tool-arg field types (shared constraints reused by tool arg schemas)
# ---------------------------------------------------------------------------

LogLevel = Literal["DEBUG", "INFO", "WARN", "WARNING", "ERROR", "CRITICAL"]


class TimeWindowArgs(BaseModel):
    """Common time-window arguments for range queries / log searches."""

    time_range: str = Field(
        default="5m",
        description="Relative window ending now, e.g. '5m', '1h', '24h'.",
    )
    step: str | None = Field(
        default=None,
        description="Optional sampling step for range queries, e.g. '30s', '1m'.",
    )
    limit: int = Field(
        default=100,
        ge=1,
        le=1000,
        description="Maximum number of items to return (hard-capped at 1000).",
    )
