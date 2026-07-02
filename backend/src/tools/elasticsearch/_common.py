"""Small helpers shared by the six Elasticsearch ``@tool`` wrappers.

Kept deliberately thin — only the profile-resolution seam and the two JSON error shapes every tool
returns, so the per-tool query logic stays readable and independently reviewable.
"""

from __future__ import annotations

import json

from src.tools.elasticsearch.shared.profiles import LogInvestigationProfile, get_profile
from src.tools.elasticsearch.shared.system_type import resolve_system_type


def resolve_profile(system_id: str | None, system_type: str | None) -> LogInvestigationProfile:
    """Resolve the classification profile: explicit ``system_type`` -> ``system_id`` lookup ->
    generic fallback (all handled by :func:`resolve_system_type` + :func:`get_profile`).
    """
    return get_profile(resolve_system_type(system_id, explicit=system_type))


def invalid_request(reason: str, *, suggestion: str | None = None, **extra: object) -> str:
    """Serialize a structured ``invalid_request`` result (safe to show the user verbatim)."""
    payload: dict[str, object] = {"status": "invalid_request", "reason": reason}
    if suggestion is not None:
        payload["suggestion"] = suggestion
    payload.update(extra)
    return json.dumps(payload, default=str)


def internal_error(message: str, **extra: object) -> str:
    """Serialize a generic ``error`` result.

    The *message* must be a generic, leak-free string — the real exception is logged server-side,
    never returned, because this value is relayed to the browser via ``TOOL_CALL_RESULT``.
    """
    payload: dict[str, object] = {"status": "error", "message": message}
    payload.update(extra)
    return json.dumps(payload, default=str)


__all__ = ["internal_error", "invalid_request", "resolve_profile"]
