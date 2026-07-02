"""Time-window helpers shared by the metric tools.

Prometheus range queries need absolute ``start``/``end`` timestamps, but users (and the LLM)
speak in **relative windows** — ``"5m"``, ``"3h"``, ``"7d"``. This module turns a relative
window into the ``(start, end)`` Unix-second strings that
:meth:`src.clients.prometheus.PrometheusClient.range_query` expects.

``PrometheusClient._to_unix_timestamp`` already passes through numeric strings and converts
ISO-8601, so an explicit ``end`` (ISO or Unix) is accepted verbatim and only the relative window
is expanded here.
"""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime

# ``<int><unit>`` with unit in s/m/h/d/w (e.g. "30s", "5m", "3h", "7d", "2w").
_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhdw])\s*$", re.IGNORECASE)
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_duration(text: str) -> int:
    """Parse a relative window like ``"5m"`` / ``"3h"`` / ``"7d"`` into **seconds**.

    Raises :class:`ValueError` for anything that is not ``<positive-int><s|m|h|d|w>``.
    """
    match = _DURATION_RE.match(text or "")
    if not match:
        raise ValueError(
            f"Invalid time window {text!r}: expected '<number><s|m|h|d|w>', e.g. '5m', '3h', '7d'."
        )
    value = int(match.group(1))
    if value <= 0:
        raise ValueError(f"Invalid time window {text!r}: duration must be positive.")
    return value * _UNIT_SECONDS[match.group(2).lower()]


def _now_unix() -> int:
    return int(datetime.now(UTC).timestamp())


def _coerce_end(end: str) -> int:
    """Coerce an explicit ``end`` (Unix seconds or ISO-8601) into Unix seconds.

    A non-finite float input (``"inf"``, ``"1e400"`` which overflows to inf, ``"nan"``) raises
    :class:`ValueError` — **not** ``OverflowError`` — so the caller's ``except ValueError`` turns
    it into a graceful error dict instead of an unhandled crash.

    Surrounding whitespace is stripped and a trailing UTC ``Z``/``z`` designator is normalized:
    :func:`datetime.fromisoformat` accepts an uppercase ``Z`` (Python >= 3.11) but rejects lowercase
    ``z`` and leading/trailing spaces, even though both denote a valid UTC instant.
    """
    text = end.strip()
    try:
        value = float(text)
    except ValueError:
        pass
    else:
        if not math.isfinite(value):
            raise ValueError(f"Invalid end timestamp {end!r}: must be a finite number.")
        return int(value)
    if text[-1:] in ("Z", "z"):
        text = f"{text[:-1]}+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp())


def resolve_range(
    time_range: str,
    end: str | None = None,
    *,
    now: int | None = None,
) -> tuple[str, str]:
    """Resolve a relative window to ``(start, end)`` Unix-second strings.

    ``end`` defaults to now (overridable via ``now`` for deterministic tests); ``start`` is
    ``end - parse_duration(time_range)``. Raises :class:`ValueError` on a malformed window.
    """
    end_ts = _coerce_end(end) if end else (now if now is not None else _now_unix())
    start_ts = end_ts - parse_duration(time_range)
    return str(start_ts), str(end_ts)
