"""LogClusterEngine: signature grouping, min_cluster_size filtering, deterministic ids, ranking."""

from __future__ import annotations

from src.tools.elasticsearch.shared.clustering import ClusteringConfig, LogClusterEngine
from src.tools.elasticsearch.shared.normalizer import LogNormalizer
from src.tools.elasticsearch.shared.profiles import GenericProfile


def _docs() -> tuple[list[dict[str, object]], list[str]]:
    raw: list[dict[str, object]] = []
    ids: list[str] = []
    # 3x "connection failed" (one signature), 2x "disk full" (another), 1 singleton.
    for i in range(3):
        raw.append(
            {
                "message": "connection failed",
                "system_id": "KHP",
                "@timestamp": f"2026-06-01T10:00:0{i}Z",
            }
        )
        ids.append(f"c{i}")
    for i in range(2):
        raw.append(
            {"message": "disk full", "system_id": "KHP", "@timestamp": f"2026-06-01T10:00:1{i}Z"}
        )
        ids.append(f"d{i}")
    raw.append(
        {"message": "one off oddity", "system_id": "KHP", "@timestamp": "2026-06-01T10:00:20Z"}
    )
    ids.append("s0")
    return raw, ids


def _cluster(min_size: int = 2) -> list:
    raw, ids = _docs()
    normalizer = LogNormalizer(profile=GenericProfile())
    logs = normalizer.normalize_batch(raw, ids, "idx")
    engine = LogClusterEngine(
        config=ClusteringConfig(min_cluster_size=min_size), normalizer=normalizer
    )
    return engine.cluster_logs(logs)


def test_two_clusters_singleton_filtered_and_ranked() -> None:
    clusters = _cluster(min_size=2)
    # Singleton dropped; two clusters, ranked by occurrence_count desc.
    assert len(clusters) == 2
    assert clusters[0].occurrence_count == 3
    assert clusters[1].occurrence_count == 2


def test_min_cluster_size_one_includes_singleton() -> None:
    clusters = _cluster(min_size=1)
    assert len(clusters) == 3
    assert {c.occurrence_count for c in clusters} == {3, 2, 1}


def test_cluster_ids_are_deterministic() -> None:
    first = _cluster()
    second = _cluster()
    assert [c.cluster_id for c in first] == [c.cluster_id for c in second]
    assert all(c.cluster_id.startswith("CLU-") for c in first)


def test_empty_input_returns_no_clusters() -> None:
    engine = LogClusterEngine(config=ClusteringConfig())
    assert engine.cluster_logs([]) == []


def _one_cluster(severity_order: list[str]):
    raw = [
        {
            "message": "same error line",
            "system_id": "KHP",
            "@timestamp": f"2026-06-01T10:00:0{i}Z",
            "log": {"level": lvl},
        }
        for i, lvl in enumerate(severity_order)
    ]
    ids = [f"x{i}" for i in range(len(severity_order))]
    normalizer = LogNormalizer(profile=GenericProfile())
    logs = normalizer.normalize_batch(raw, ids, "idx")
    engine = LogClusterEngine(config=ClusteringConfig(min_cluster_size=2), normalizer=normalizer)
    return engine.cluster_logs(logs)[0]


def test_dominant_severity_tie_break_is_order_independent() -> None:
    # One cluster with a 1:1 ERROR/CRITICAL tie must pick the same dominant severity regardless
    # of input order (max() alone would return whichever tied key was inserted first).
    forward = _one_cluster(["error", "critical"])
    reverse = _one_cluster(["critical", "error"])
    assert forward.dominant_severity == reverse.dominant_severity
