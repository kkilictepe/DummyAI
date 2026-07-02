"""Relative-window parsing + range resolution for the metric tools."""

from __future__ import annotations

import pytest

from src.tools._time import parse_duration, resolve_range


@pytest.mark.parametrize(
    "text,seconds",
    [
        ("30s", 30),
        ("5m", 300),
        ("3h", 10800),
        ("24h", 86400),
        ("7d", 604800),
        ("2w", 1209600),
        (" 1h ", 3600),  # surrounding whitespace tolerated
        ("2H", 7200),  # unit is case-insensitive
    ],
)
def test_parse_duration_units(text: str, seconds: int) -> None:
    assert parse_duration(text) == seconds


@pytest.mark.parametrize("bad", ["", "banana", "5", "m", "-5m", "0m", "5x", "1.5h", "5 m 3s"])
def test_parse_duration_rejects_malformed(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_duration(bad)


def test_resolve_range_with_numeric_end() -> None:
    assert resolve_range("5m", "1000") == ("700", "1000")


def test_resolve_range_now_override() -> None:
    # end omitted -> uses injected `now`, so the result is deterministic.
    assert resolve_range("5m", now=1000) == ("700", "1000")


def test_resolve_range_iso_end_is_treated_as_utc() -> None:
    naive = resolve_range("1h", "2026-07-01T00:00:00")
    aware = resolve_range("1h", "2026-07-01T00:00:00+00:00")
    zulu = resolve_range("1h", "2026-07-01T00:00:00Z")
    assert naive == aware == zulu
    start, end = zulu
    assert int(end) - int(start) == 3600


@pytest.mark.parametrize(
    "end",
    [
        "2026-07-01T00:00:00Z",  # canonical uppercase designator
        "2026-07-01T00:00:00z",  # lowercase 'z' — fromisoformat rejects it raw
        " 2026-07-01T00:00:00Z ",  # surrounding whitespace
        "  2026-07-01T00:00:00+00:00  ",  # whitespace around an explicit offset
    ],
)
def test_resolve_range_iso_end_tolerates_z_case_and_whitespace(end: str) -> None:
    # All of these denote the same valid UTC instant, so they must resolve identically to the
    # canonical uppercase-Z form (and never raise) — the LLM/user may phrase 'end' any of these.
    assert resolve_range("1h", end) == resolve_range("1h", "2026-07-01T00:00:00Z")


def test_resolve_range_default_end_uses_now() -> None:
    start, end = resolve_range("5m")
    assert int(end) - int(start) == 300
    assert int(end) > 0


def test_resolve_range_rejects_bad_window() -> None:
    with pytest.raises(ValueError):
        resolve_range("banana", "1000")


@pytest.mark.parametrize("end", ["inf", "-inf", "infinity", "1e400", "nan"])
def test_resolve_range_rejects_non_finite_end_with_valueerror(end: str) -> None:
    # non-finite ends must raise ValueError (NOT OverflowError) so the tool's ValueError guard
    # turns them into a graceful error instead of an unhandled crash.
    with pytest.raises(ValueError):
        resolve_range("5m", end)
