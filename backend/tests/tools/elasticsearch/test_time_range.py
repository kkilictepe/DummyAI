"""time_range parsing: relative durations, absolute ISO pairs, and error cases."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.tools.elasticsearch.shared.time_range import parse_relative_time_range, parse_time_range

_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)


def test_relative_range_ends_at_now() -> None:
    start, end = parse_relative_time_range("3h", now=_NOW)
    assert end == _NOW
    assert (end - start).total_seconds() == 3 * 3600


def test_parse_time_range_relative_units() -> None:
    for spec, seconds in [("30m", 1800), ("2h", 7200), ("1d", 86400), ("1w", 604800)]:
        start, end = parse_time_range(spec, now=_NOW)
        assert (end - start).total_seconds() == seconds


def test_parse_time_range_iso_pair() -> None:
    start, end = parse_time_range("2026-04-22T17:25:00Z/2026-04-22T20:25:30Z")
    assert start == datetime(2026, 4, 22, 17, 25, 0, tzinfo=UTC)
    assert end == datetime(2026, 4, 22, 20, 25, 30, tzinfo=UTC)


def test_parse_time_range_naive_iso_treated_as_utc() -> None:
    start, _end = parse_time_range("2026-04-22T17:25:00/2026-04-22T20:25:30")
    assert start.tzinfo is not None and start.utcoffset() == UTC.utcoffset(None)


def test_iso_pair_must_be_ordered() -> None:
    with pytest.raises(ValueError, match="strictly before"):
        parse_time_range("2026-04-22T20:00:00Z/2026-04-22T17:00:00Z")


def test_invalid_format_raises() -> None:
    with pytest.raises(ValueError, match="Invalid time_range"):
        parse_time_range("banana")
    with pytest.raises(ValueError, match="Invalid time_range"):
        parse_time_range("2026-04-22T20:00:00Z/not-a-date")
