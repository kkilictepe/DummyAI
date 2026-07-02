"""Time-range parsing for the Elasticsearch tools.

Extracted from the reference ``deterministic_elasticsearch_investigation/_internal/time_range.py``
(the orchestrator that owned it is out of scope). Accepts a relative duration (``3h``, ``24h``,
``7d``, ``30m``, ``1w``) ending at ``now``, or an absolute ISO-8601 pair ``<iso>/<iso>``.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

_RELATIVE_RE = re.compile(r"(\d+)\s*([mhdw])")


def parse_relative_time_range(
    time_range: str, *, now: datetime | None = None
) -> tuple[datetime, datetime]:
    """Parse a relative range string into ``(from_time, to_time)`` UTC datetimes.

    Supports ``3h`` / ``24h`` / ``7d`` / ``30m`` / ``1w``. ``now`` is injectable for deterministic
    tests.
    """
    if not isinstance(time_range, str):
        raise TypeError("time_range must be a str")

    to_time = now or datetime.now(UTC)
    normalized = time_range.strip().lower()

    match = re.fullmatch(_RELATIVE_RE, normalized)
    if not match:
        raise ValueError(
            f"Invalid time_range format: '{normalized}'. "
            "Expected format like '3h', '24h', '7d', '30m', or '1w'."
        )

    value = int(match.group(1))
    unit = match.group(2)
    unit_map = {
        "m": timedelta(minutes=value),
        "h": timedelta(hours=value),
        "d": timedelta(days=value),
        "w": timedelta(weeks=value),
    }
    return (to_time - unit_map[unit], to_time)


def parse_iso_utc(value: str) -> datetime:
    """Parse a single ISO-8601 datetime, returning a UTC-aware datetime.

    Accepts surrounding whitespace and a trailing 'Z'/'z' (zulu) — neither of which
    :func:`datetime.fromisoformat` tolerates on its own even though both denote a valid UTC
    instant — and treats a naive datetime as UTC. Shared by every ES tool that parses an
    LLM/user-supplied ISO timestamp so they all accept the same set of valid forms.
    """
    s = value.strip()
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def parse_time_range(time_range: str, *, now: datetime | None = None) -> tuple[datetime, datetime]:
    """Parse a time range into ``(from_time, to_time)`` UTC datetimes.

    Accepts a relative duration (window ends at ``now``) or an absolute ISO-8601 pair
    ``<iso>/<iso>``. Trailing 'Z'/'z' is accepted; naive datetimes are assumed UTC.

    Raises ``ValueError`` if the string matches neither format, or the ISO pair is malformed or not
    chronologically ordered.
    """
    if not isinstance(time_range, str):
        raise TypeError("time_range must be a str")

    stripped = time_range.strip()

    # ISO pair '<iso>/<iso>' — detected by a '/' separator. Look at the raw (not-lowercased) value
    # so the ISO path preserves case on parts that are not the 'Z' suffix.
    if "/" in stripped:
        parts = stripped.split("/", 1)
        if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
            raise ValueError(
                f"Invalid time_range format: '{time_range}'. "
                "ISO pair must be '<iso>/<iso>', e.g. "
                "'2026-04-22T17:25:00Z/2026-04-22T20:25:30Z'."
            )
        try:
            from_time = parse_iso_utc(parts[0])
            to_time = parse_iso_utc(parts[1])
        except ValueError as exc:
            raise ValueError(
                f"Invalid time_range format: '{time_range}'. "
                f"ISO pair could not be parsed: {exc}. Expected "
                "'<iso>/<iso>', e.g. '2026-04-22T17:25:00Z/2026-04-22T20:25:30Z'."
            ) from exc
        if from_time >= to_time:
            raise ValueError(
                f"Invalid time_range: '{time_range}'. ISO pair start must be strictly before end."
            )
        return from_time, to_time

    # Relative duration — delegate to the strict parser for shared semantics.
    try:
        return parse_relative_time_range(stripped, now=now)
    except ValueError as exc:
        raise ValueError(
            f"Invalid time_range format: '{time_range}'. "
            "Expected a relative duration like '3h', '24h', '7d', '30m', '1w', "
            "or an ISO pair '<iso>/<iso>' like "
            "'2026-04-22T17:25:00Z/2026-04-22T20:25:30Z'."
        ) from exc


__all__ = ["parse_iso_utc", "parse_relative_time_range", "parse_time_range"]
