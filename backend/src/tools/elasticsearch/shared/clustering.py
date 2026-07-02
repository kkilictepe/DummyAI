"""Deterministic log clustering engine.

Clusters logs by message signature, temporal proximity, and system/component alignment. All
operations are stateless and deterministic: same input -> same output, no LLM calls, no random
sampling. Cluster ids are ``uuid5`` of the group key, so they are stable across runs.

Only the ``cluster_logs`` path (used by the ``es_cluster_errors`` tool) is ported; the reference's
optional ``merge_related_clusters`` post-processing (which minted non-deterministic ``uuid4`` ids)
belonged to the excluded orchestrator and is intentionally omitted.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Callable
from datetime import timedelta

from src.logging import get_logger
from src.tools.elasticsearch.shared.normalizer import LogNormalizer
from src.tools.elasticsearch.shared.schemas import (
    ErrorCategory,
    LogCluster,
    NormalizedLog,
    SampleLogEntry,
)

_log = get_logger(__name__)

_MAX_SAMPLE_DOC_IDS = 20


class ClusteringConfig:
    """Configuration for clustering behaviour."""

    def __init__(
        self,
        temporal_window_seconds: int = 300,
        min_cluster_size: int = 2,
        max_samples_per_cluster: int = 5,
        enable_temporal_subclustering: bool = True,
        signature_similarity_threshold: float = 1.0,  # Exact match by default
    ) -> None:
        self.temporal_window_seconds = temporal_window_seconds
        self.min_cluster_size = min_cluster_size
        self.max_samples_per_cluster = max_samples_per_cluster
        self.enable_temporal_subclustering = enable_temporal_subclustering
        self.signature_similarity_threshold = signature_similarity_threshold


class LogClusterEngine:
    """Deterministic log clustering engine."""

    def __init__(
        self,
        config: ClusteringConfig | None = None,
        normalizer: LogNormalizer | None = None,
    ) -> None:
        self.config = config or ClusteringConfig()
        self.normalizer = normalizer or LogNormalizer()

    def cluster_logs(
        self,
        logs: list[NormalizedLog],
        group_by_system: bool = True,
        group_by_component: bool = False,
    ) -> list[LogCluster]:
        """Cluster normalized logs into logical groups, ranked by occurrence count (desc)."""
        if not logs:
            return []

        # Phase 1: group by signature.
        signature_groups = self._group_by_signature(logs)

        # Phase 2: optionally split by system.
        if group_by_system:
            signature_groups = self._split_by_dimension(signature_groups, lambda log: log.system)

        # Phase 3: optionally split by component.
        if group_by_component:
            signature_groups = self._split_by_dimension(
                signature_groups, lambda log: log.component or "UNKNOWN"
            )

        # Phase 4: temporal subclustering (split large clusters with time gaps).
        if self.config.enable_temporal_subclustering:
            signature_groups = self._temporal_subcluster(signature_groups)

        # Phase 5: convert to LogCluster objects.
        clusters: list[LogCluster] = []
        for group_key, group_logs in signature_groups.items():
            if len(group_logs) >= self.config.min_cluster_size:
                clusters.append(self._create_cluster(group_key, group_logs))

        # Sort clusters by occurrence count (descending).
        clusters.sort(key=lambda c: c.occurrence_count, reverse=True)

        _log.debug("clusters_created", clusters=len(clusters), logs=len(logs))
        return clusters

    def _group_by_signature(self, logs: list[NormalizedLog]) -> dict[str, list[NormalizedLog]]:
        """Group logs by their message signature."""
        groups: dict[str, list[NormalizedLog]] = defaultdict(list)
        for log in logs:
            groups[log.message_signature].append(log)
        return dict(groups)

    def _split_by_dimension(
        self,
        groups: dict[str, list[NormalizedLog]],
        dimension_fn: Callable[[NormalizedLog], str],
    ) -> dict[str, list[NormalizedLog]]:
        """Split existing groups by an additional dimension, producing composite keys."""
        new_groups: dict[str, list[NormalizedLog]] = defaultdict(list)
        for sig_key, logs in groups.items():
            for log in logs:
                composite_key = f"{sig_key}|{dimension_fn(log)}"
                new_groups[composite_key].append(log)
        return dict(new_groups)

    def _temporal_subcluster(
        self, groups: dict[str, list[NormalizedLog]]
    ) -> dict[str, list[NormalizedLog]]:
        """Split groups whose logs are separated by more than ``temporal_window_seconds``."""
        new_groups: dict[str, list[NormalizedLog]] = {}
        window = timedelta(seconds=self.config.temporal_window_seconds)

        for group_key, logs in groups.items():
            if len(logs) <= 1:
                new_groups[group_key] = logs
                continue

            sorted_logs = sorted(logs, key=lambda log_entry: log_entry.timestamp)

            current_subgroup: list[NormalizedLog] = [sorted_logs[0]]
            subgroup_counter = 0

            for i in range(1, len(sorted_logs)):
                time_gap = sorted_logs[i].timestamp - sorted_logs[i - 1].timestamp
                if time_gap > window:
                    subgroup_key = f"{group_key}#T{subgroup_counter}"
                    new_groups[subgroup_key] = current_subgroup
                    current_subgroup = [sorted_logs[i]]
                    subgroup_counter += 1
                else:
                    current_subgroup.append(sorted_logs[i])

            if current_subgroup:
                if subgroup_counter == 0:
                    new_groups[group_key] = current_subgroup
                else:
                    subgroup_key = f"{group_key}#T{subgroup_counter}"
                    new_groups[subgroup_key] = current_subgroup

        return new_groups

    def _create_cluster(self, group_key: str, logs: list[NormalizedLog]) -> LogCluster:
        """Create a ``LogCluster`` from a group of logs with full metadata."""
        # Deterministic cluster ID from the group key.
        cluster_id = f"CLU-{uuid.uuid5(uuid.NAMESPACE_DNS, group_key).hex[:12]}"

        signature = logs[0].message_signature
        signature_pattern = self.normalizer.get_signature_pattern(logs[0].raw_message)

        sorted_logs = sorted(logs, key=lambda log_entry: log_entry.timestamp)

        unique_systems = sorted({log.system for log in logs})
        unique_hosts = sorted({log.host for log in logs if log.host})
        unique_users = sorted({log.user for log in logs if log.user})

        first_seen = sorted_logs[0].timestamp
        last_seen = sorted_logs[-1].timestamp

        avg_interval: float | None = None
        if len(sorted_logs) > 1:
            total_duration = (last_seen - first_seen).total_seconds()
            avg_interval = total_duration / (len(sorted_logs) - 1)

        # Tie-break dominant severity/category on the key so the result is order-independent
        # (max() otherwise returns whichever tied key was inserted first, i.e. input order).
        severity_dist: dict[str, int] = defaultdict(int)
        for log in logs:
            severity_dist[log.severity] += 1
        dominant_severity = max(severity_dist.items(), key=lambda x: (x[1], x[0]))[0]

        error_cat_dist: dict[str, int] = defaultdict(int)
        for log in logs:
            error_cat_dist[log.error_category.value] += 1
        dominant_error_cat = max(error_cat_dist.items(), key=lambda x: (x[1], x[0]))[0]

        component_dist: dict[str, int] = defaultdict(int)
        for log in logs:
            component_dist[log.component or "UNKNOWN"] += 1

        transaction_dist: dict[str, int] = defaultdict(int)
        for log in logs:
            if log.transaction:
                transaction_dist[log.transaction] += 1

        selected = self._select_representative_samples(
            sorted_logs, self.config.max_samples_per_cluster
        )
        sample_logs = [
            SampleLogEntry(
                doc_id=log.doc_id,
                timestamp=log.timestamp,
                severity=log.severity,
                raw_message=log.raw_message,
                error_category=log.error_category,
            )
            for log in selected
        ]

        all_doc_ids = [log.doc_id for log in logs]

        return LogCluster(
            cluster_id=cluster_id,
            signature=signature,
            signature_pattern=signature_pattern,
            occurrence_count=len(logs),
            unique_systems=unique_systems,
            unique_hosts=unique_hosts,
            unique_users=unique_users,
            first_seen=first_seen,
            last_seen=last_seen,
            avg_interval_seconds=avg_interval,
            severity_distribution=dict(severity_dist),
            dominant_severity=dominant_severity,
            error_category=ErrorCategory(dominant_error_cat),
            component_distribution=dict(component_dist),
            transaction_distribution=dict(transaction_dist),
            sample_logs=sample_logs,
            sample_doc_ids=all_doc_ids[:_MAX_SAMPLE_DOC_IDS],
        )

    def _select_representative_samples(
        self,
        logs: list[NormalizedLog],
        max_samples: int,
    ) -> list[NormalizedLog]:
        """Pick representative samples deterministically: first, last, then evenly spaced."""
        if len(logs) <= max_samples:
            return logs

        samples: list[NormalizedLog] = [logs[0]]
        if logs[-1] is not logs[0]:
            samples.append(logs[-1])

        remaining_slots = max_samples - len(samples)
        if remaining_slots > 0 and len(logs) > 2:
            middle_logs = logs[1:-1]
            step = max(1, len(middle_logs) // (remaining_slots + 1))
            for i in range(remaining_slots):
                idx = min((i + 1) * step, len(middle_logs) - 1)
                if middle_logs[idx] not in samples:
                    samples.append(middle_logs[idx])

        samples.sort(key=lambda sample: sample.timestamp)
        return samples[:max_samples]
