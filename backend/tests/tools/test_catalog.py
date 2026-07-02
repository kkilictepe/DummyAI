"""MetricCatalog wrapper over the committed catalog + profiles."""

from __future__ import annotations

from src.tools._catalog import MetricCatalog


def test_entry_lookup_and_prometheus_name() -> None:
    catalog = MetricCatalog.load()
    assert catalog.prometheus_name("hana_cpu_utilisation") == "hana_cpu_utilization_percent"
    entry = catalog.entry("hana_cpu_utilisation")
    assert entry is not None
    assert entry.unit == "%"


def test_unknown_key_returns_none() -> None:
    catalog = MetricCatalog.load()
    assert catalog.entry("does_not_exist") is None
    assert catalog.prometheus_name("does_not_exist") is None


def test_profiles_and_keys() -> None:
    catalog = MetricCatalog.load()
    assert catalog.has_profile("cpu_overview")
    assert not catalog.has_profile("does_not_exist")
    assert catalog.profile_keys("does_not_exist") == []

    keys = catalog.profile_keys("cpu_overview")
    assert "sap_application_cpu_utilisation" in keys
    assert "hana_cpu_utilisation" in keys
    assert "cpu_overview" in catalog.profiles()


def test_alert_threshold_stays_str_or_none() -> None:
    catalog = MetricCatalog.load()
    # Numeric threshold stays a *string* ('5', not 5); '-' is preserved verbatim (the summarizer
    # treats it as "not applicable"); only empty/missing coerces to None (config field validator).
    shortdumps = catalog.entry("sap_application_abap_shortdumps_frequency")
    assert shortdumps is not None
    assert shortdumps.alert_threshold == "5"

    bgrfc = catalog.entry("sap_application_bgrfc_inbound_throughput_actual")
    assert bgrfc is not None
    assert bgrfc.alert_threshold == "-"  # '-' in YAML is kept as-is

    heap_total = catalog.entry("sap_application_memory_heap_total")
    assert heap_total is not None
    assert heap_total.alert_threshold is None  # empty in YAML -> None
