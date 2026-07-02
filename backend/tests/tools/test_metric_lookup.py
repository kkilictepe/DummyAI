"""Deterministic metric_lookup: relevance, profile/category scoping, cap, stable ordering."""

from __future__ import annotations

from src.tools.metric_lookup import CatalogSearchResolver, MetricCandidate, metric_lookup


def test_high_cpu_ranks_both_cpu_metrics_first() -> None:
    out = metric_lookup.invoke({"query": "high cpu"})
    assert set(out["prometheus_names"][:2]) == {
        "hana_cpu_utilization_percent",
        "sap_application_cpu_utilisation_percent",
    }


def test_short_dump_phrase_finds_shortdumps_metric() -> None:
    out = metric_lookup.invoke({"query": "abap short dump"})
    assert "sap_application_abap_shortdumps_frequency_per_min" in out["prometheus_names"]


def test_profile_restricts_candidate_pool() -> None:
    # 'cpu' inside the HANA profile must not surface the SAP application CPU metric.
    out = metric_lookup.invoke({"query": "cpu", "profile": "hana_db_resource_usage"})
    assert out["prometheus_names"] == ["hana_cpu_utilization_percent"]


def test_category_filter_restricts_to_catalog_category() -> None:
    out = metric_lookup.invoke({"query": "memory", "category": "hana_db_resource_usage"})
    assert out["prometheus_names"] == ["hana_memory_utilization_percent"]


def test_top_k_is_capped() -> None:
    out = metric_lookup.invoke({"query": "sap", "top_k": 3})
    assert len(out["candidates"]) <= 3


def test_ranking_is_stable_on_ties() -> None:
    # hana_cpu_utilisation and sap_application_cpu_utilisation tie on score; the logical-key
    # tiebreak puts 'hana_...' first, deterministically, every run.
    keys = [
        c["logical_key"] for c in metric_lookup.invoke({"query": "cpu", "top_k": 2})["candidates"]
    ]
    assert keys[0] == "hana_cpu_utilisation"


def test_prometheus_names_are_deduplicated_and_ordered() -> None:
    out = metric_lookup.invoke({"query": "memory", "top_k": 10})
    names = out["prometheus_names"]
    assert len(names) == len(set(names))  # no duplicates
    # names is the order-preserving projection of candidate prometheus_names
    expected: list[str] = []
    for candidate in out["candidates"]:
        if candidate["prometheus_name"] not in expected:
            expected.append(candidate["prometheus_name"])
    assert names == expected


def test_resolver_returns_scored_candidates() -> None:
    candidates = CatalogSearchResolver().search("high cpu", top_k=2)
    assert candidates
    assert all(isinstance(c, MetricCandidate) for c in candidates)
    assert candidates[0].score > 0
    # scores are monotonically non-increasing (sorted descending)
    assert candidates[0].score >= candidates[-1].score


def test_no_matches_returns_empty() -> None:
    out = metric_lookup.invoke({"query": "zzznonsensequery"})
    assert out["candidates"] == []
    assert out["prometheus_names"] == []


def test_stopword_only_query_seeds_from_profile() -> None:
    # "show me the" tokenizes to only stopwords -> empty token set -> the seeding branch returns
    # the whole (profile-filtered) pool at score 0, so a bare profile lookup still yields metrics.
    out = metric_lookup.invoke({"query": "show me the", "profile": "hana_db_resource_usage"})
    names = set(out["prometheus_names"])
    assert names == {
        "hana_memory_utilization_percent",
        "hana_disc_utilization_percent",
        "hana_cpu_utilization_percent",
    }
    assert all(c["score"] == 0 for c in out["candidates"])


def test_profile_and_category_filters_compose() -> None:
    # profile narrows to cpu_overview's keys, then category further filters to the HANA category;
    # only hana_cpu_utilisation is in both, so it is the sole survivor.
    out = metric_lookup.invoke(
        {"query": "cpu", "profile": "cpu_overview", "category": "hana_db_resource_usage"}
    )
    assert out["prometheus_names"] == ["hana_cpu_utilization_percent"]
