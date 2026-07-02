"""SafePromQLBuilder: safe selector construction + label-value escaping (no injection)."""

from __future__ import annotations

from src.tools.prometheus_advanced_query.promql_builder import SafePromQLBuilder

_BUILDER = SafePromQLBuilder()


def test_bare_metric_when_no_labels() -> None:
    assert _BUILDER.build_instant_query("sap_cpu") == "sap_cpu"


def test_labels_sorted_and_wrapped() -> None:
    # Labels are emitted in sorted key order for deterministic, cache-friendly queries.
    query = _BUILDER.build_instant_query("sap_cpu", {"system_id": "KHP", "instance": "app01"})
    assert query == 'sap_cpu{instance="app01", system_id="KHP"}'


def test_label_value_is_escaped_against_breakout() -> None:
    # A value carrying a quote + brace must be escaped so it cannot terminate the label string.
    query = _BUILDER.build_instant_query("sap_cpu", {"system_id": 'KHP"} evil{'})
    assert query == 'sap_cpu{system_id="KHP\\"} evil{"}'


def test_backslash_escaped_before_quote() -> None:
    # Order matters: backslash first, else the escape of a following quote would be undone.
    query = _BUILDER.build_instant_query("m", {"k": 'a\\"b'})
    assert query == 'm{k="a\\\\\\"b"}'


def test_newline_and_cr_escaped() -> None:
    query = _BUILDER.build_instant_query("m", {"k": "a\nb\rc"})
    assert query == 'm{k="a\\nb\\rc"}'


def test_multi_metric_query_one_selector_each() -> None:
    queries = _BUILDER.build_multi_metric_query(["sap_cpu", "hana_cpu"], {"system_id": "KHP"})
    assert queries == [
        ("sap_cpu", 'sap_cpu{system_id="KHP"}'),
        ("hana_cpu", 'hana_cpu{system_id="KHP"}'),
    ]
