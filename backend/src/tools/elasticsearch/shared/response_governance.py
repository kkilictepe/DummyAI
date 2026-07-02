"""Response governance helpers for the Elasticsearch tool layer.

Provides projection, byte-cap, metadata, and field-validation primitives consumed by the six ES
primitive tools. Every tool return value is relayed to the browser via the AG-UI
``TOOL_CALL_RESULT`` event, so these helpers keep payloads bounded (256 KB) and JSON-parseable.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Collection
from typing import Any, Literal

from src.tools.elasticsearch.shared.profiles.base import LogInvestigationProfile


def coerce_json_object_arg(value: Any, field_name: str) -> dict[str, Any] | str:
    """Coerce an LLM-supplied JSON string (or test-supplied dict) to a dict.

    Returns the parsed dict for valid input, or an ``__parse_error__:<detail>`` sentinel string the
    caller can detect to emit a structured invalid_request without touching Elasticsearch. ``None``
    and empty strings yield ``{}``.

    The LLM-facing Pydantic schema for these fields is ``str | None`` so it stays compatible with
    strict tool-calling modes (free-form ``additionalProperties: true`` objects are rejected under
    strict=True). The dict path keeps tests that pass native dicts working without change.
    """
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        if not value.strip():
            return {}
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            return f"__parse_error__:{exc}"
        if not isinstance(parsed, dict):
            return f"__parse_error__:{field_name} JSON must decode to an object"
        return parsed
    return f"__parse_error__:{field_name} must be a JSON object string"


MINIMAL_PROJECTION_BASELINE: frozenset[str] = frozenset(
    {"@timestamp", "message", "msg_text", "log.level"}
)


class InvalidRequestError(dict[str, Any]):
    """``dict`` with ``status='invalid_request'`` plus ``reason`` / ``suggestion``."""

    status: Literal["invalid_request"]
    reason: str
    suggestion: str


def apply_minimal_projection(fields_to_return: Collection[str]) -> list[str]:
    """Return the sorted union of baseline fields and caller-requested fields."""
    return sorted(MINIMAL_PROJECTION_BASELINE | set(fields_to_return))


def enforce_byte_cap(payload: str, cap: int = 256_000) -> tuple[str, bool, str | None]:
    """Truncate *payload* to at most *cap* UTF-8 bytes.

    Returns ``(result, truncated, reason)``. Truncation walks back to the last valid multi-byte
    boundary via ``errors='ignore'``.
    """
    encoded = payload.encode("utf-8")
    if len(encoded) <= cap:
        return (payload, False, None)
    truncated = encoded[:cap].decode("utf-8", errors="ignore")
    return (truncated, True, f"Payload exceeded {cap} bytes")


def fit_items_to_cap(
    items: list[Any],
    build_json: Callable[[list[Any], bool, str | None], str],
    cap: int = 256_000,
) -> tuple[str, int, bool, str | None]:
    """Binary-search the longest prefix of *items* whose serialized payload fits *cap*.

    ``build_json(items_slice, truncated, truncated_reason)`` must return the full serialized JSON
    string (including response_meta) for the given slice and truncation state, keeping the returned
    bytes and response_meta consistent with the governance contract.

    Returns ``(result_str, fitted_count, truncated, truncated_reason)``.
    """
    full = build_json(items, False, None)
    if len(full.encode("utf-8")) <= cap:
        return full, len(items), False, None

    reason = f"Payload exceeded {cap} bytes; items reduced to fit cap"
    lo, hi = 0, len(items)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        candidate = build_json(items[:mid], True, reason)
        if len(candidate.encode("utf-8")) <= cap:
            lo = mid
        else:
            hi = mid - 1
    return build_json(items[:lo], True, reason), lo, True, reason


def build_response_meta(
    hit_count: int,
    query_time_ms: float,
    token_estimate: int,
    truncated: bool,
    truncated_reason: str | None = None,
) -> dict[str, Any]:
    """Return the standard 5-key response metadata dict."""
    return {
        "hit_count": hit_count,
        "query_time_ms": query_time_ms,
        "token_estimate": token_estimate,
        "truncated": truncated,
        "truncated_reason": truncated_reason,
    }


def validate_field_filters(
    must_match: dict[str, Any],
    profile: LogInvestigationProfile,
) -> InvalidRequestError | None:
    """Return None if all keys in *must_match* are searchable; else an error dict."""
    known = profile.searchable_fields()
    unknown = set(must_match.keys()) - known
    if not unknown:
        return None
    return InvalidRequestError(
        status="invalid_request",
        reason=f"Unknown field(s) in must_match: {sorted(unknown)!r}",
        suggestion=f"Searchable fields for {profile.system_type!r}: {sorted(known)!r}",
    )


__all__ = [
    "MINIMAL_PROJECTION_BASELINE",
    "InvalidRequestError",
    "apply_minimal_projection",
    "build_response_meta",
    "coerce_json_object_arg",
    "enforce_byte_cap",
    "fit_items_to_cap",
    "validate_field_filters",
]
