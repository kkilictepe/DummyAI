"""Live validation of a caller-supplied ``monitoring_context`` against Prometheus.

The categorized-metrics tool lets the operator pin a ``monitoring_context`` (an app-server /
instance id) to narrow a query to one SAP instance. A typo previously produced a silent
``no_data`` result — indistinguishable from "the metric genuinely has no data". This module
validates the value against the ``monitoring_context`` label values Prometheus actually has for the
system, so the tool can instead return a helpful ``invalid_label_filter`` error with a suggestion.

**Fail-open**: validation may only *add* a helpful error, never break a working tool. If the
available-values list cannot be discovered (empty — Prometheus down, label absent, transient
error), validation is skipped and the tool proceeds exactly as before.

The discovered values are cached per system for a short TTL (Prometheus label discovery is a real
HTTP round-trip and app-server rosters change slowly). An **empty** result is never cached, so a
transient discovery failure cannot pin validation off for the whole TTL.
"""

from __future__ import annotations

from difflib import get_close_matches
from time import monotonic
from typing import TYPE_CHECKING, Any

from src.logging import get_logger

if TYPE_CHECKING:
    from src.clients.prometheus import PrometheusClient

_log = get_logger(__name__)

_CACHE_TTL_SECONDS = 300.0

# system_id -> (fetched_at_monotonic, available monitoring_context values). Module-level so the TTL
# survives across tool calls; ``reset_context_cache`` clears it for hermetic tests.
_context_cache: dict[str, tuple[float, list[str]]] = {}


def reset_context_cache() -> None:
    """Clear the monitoring_context discovery cache (used by tests)."""
    _context_cache.clear()


def _series_selector(system_id: str) -> str:
    """A ``{system_id="X"}`` PromQL series selector with the value escaped.

    Escaped identically to ``tool._build_promql`` (backslash then quote) — duplicated as a
    one-liner to keep this lower-level module free of an import cycle with ``tool``.
    """
    escaped = system_id.replace("\\", "\\\\").replace('"', '\\"')
    return f'{{system_id="{escaped}"}}'


async def get_monitoring_contexts(client: PrometheusClient, system_id: str) -> list[str]:
    """Return the ``monitoring_context`` label values Prometheus has for ``system_id``.

    Cached for ``_CACHE_TTL_SECONDS`` per system (monotonic clock). An empty list is never cached —
    a transient discovery failure must not disable validation for the whole TTL.
    """
    now = monotonic()
    cached = _context_cache.get(system_id)
    if cached is not None and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    values = await client.label_values("monitoring_context", match=[_series_selector(system_id)])
    if values:
        _context_cache[system_id] = (now, values)
    return values


async def validate_monitoring_context(
    client: PrometheusClient, system_id: str, value: str
) -> dict[str, Any] | None:
    """Validate a pinned ``monitoring_context`` against the system's live label values.

    Returns ``None`` when the value is valid **or unverifiable** (fail-open: an empty
    available-list skips validation). Otherwise returns an error fragment the tool merges into its
    ``invalid_label_filter`` envelope::

        {"error": "<human sentence with suggestion>",
         "invalid_filters": [{"label": "monitoring_context", "provided": ...,
                              "available": [...], "suggestion": "..." | None}]}

    The suggestion is the single closest available value (stdlib ``difflib``, cutoff 0.6). Every
    field is JSON-safe and leak-free — ``available`` holds the same values already surfaced as
    ``available_application_servers``.
    """
    available = await get_monitoring_contexts(client, system_id)
    if not available:
        _log.debug("monitoring_context_validation_skipped", system_id=system_id)
        return None  # unverifiable -> skip (fail-open)
    if value in available:
        return None

    matches = get_close_matches(value, available, n=1, cutoff=0.6)
    suggestion = matches[0] if matches else None

    message = (
        f"The monitoring_context '{value}' does not exist for system_id '{system_id}'. "
        f"Available values: {', '.join(available)}."
    )
    if suggestion:
        message += f" Did you mean '{suggestion}'?"

    return {
        "error": message,
        "invalid_filters": [
            {
                "label": "monitoring_context",
                "provided": value,
                "available": available,
                "suggestion": suggestion,
            }
        ],
    }


__all__ = [
    "get_monitoring_contexts",
    "reset_context_cache",
    "validate_monitoring_context",
]
