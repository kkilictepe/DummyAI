"""Cross-tool primitive schemas for Elasticsearch log analysis.

These types are the shared vocabulary every Elasticsearch tool, profile, clustering engine,
and downstream consumer speaks. Tool-specific request/report schemas live next to their owning
tool. Ported from the reference ``elasticsearch/shared/schemas.py`` with modern typing and
``StrEnum`` (str+Enum inheritance is rejected by ruff UP042).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

# =============================================================================
# ENUMS
# =============================================================================


class LogSeverity(StrEnum):
    """SAP log severity levels."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"
    ABORT = "A"  # SAP ABAP abort
    EXCEPTION = "X"  # SAP exception
    DUMP = "DUMP"  # Short dump


class ErrorCategory(StrEnum):
    """Error taxonomy used by all profiles.

    Cross-system members are unprefixed; system-family members carry a prefix (e.g. ``SAP_*``).
    Profiles populate the categories they understand; anything else falls through to ``UNKNOWN``.
    """

    # --- Cross-system ---
    DB = "DB"  # Database errors
    TIMEOUT = "TIMEOUT"  # Timeout errors
    MEMORY = "MEMORY"  # Memory allocation / OOM
    LOCK = "LOCK"  # Lock / deadlock / enqueue
    AUTHORIZATION = "AUTHORIZATION"  # Auth failures
    BATCH = "BATCH"  # Background / scheduled job failures
    NETWORK = "NETWORK"  # Connection refused/reset, DNS, TLS, certificates
    CONFIG = "CONFIG"  # Configuration / missing setting
    UNKNOWN = "UNKNOWN"  # Unclassified

    # --- SAP-specific (prefixed SAP_*) ---
    SAP_DUMP = "SAP_DUMP"  # ABAP short dumps (ST22 / RABAX)
    SAP_RFC = "SAP_RFC"  # RFC connection errors (CPIC, NI_, COMMUNICATION_FAILURE)
    SAP_ICM = "SAP_ICM"  # ICM / HTTP errors
    SAP_GATEWAY = "SAP_GATEWAY"  # Gateway errors
    SAP_TRANSPORT = "SAP_TRANSPORT"  # Transport / STMS errors


class AnomalyType(StrEnum):
    """Types of detected anomalies."""

    SPIKE = "spike"  # Sudden increase in error frequency
    CASCADE = "cascade"  # Error propagation pattern
    BASELINE_EXCEEDED = "baseline_exceeded"  # Above historical threshold
    NEW_ERROR = "new_error"  # Previously unseen error pattern
    CORRELATION = "correlation"  # Correlated with other events


class HypothesisConfidence(StrEnum):
    """Confidence levels for root cause hypotheses."""

    HIGH = "high"  # 0.7 - 1.0
    MEDIUM = "medium"  # 0.4 - 0.69
    LOW = "low"  # 0.0 - 0.39


# =============================================================================
# NORMALIZED LOG + SAMPLE
# =============================================================================


class NormalizedLog(BaseModel):
    """Unified internal log structure after normalization.

    All logs from ES are normalized to this format for consistent processing.
    """

    # Identity
    doc_id: str = Field(..., description="Elasticsearch document ID")
    index_name: str = Field(..., description="Source Elasticsearch index")

    # Temporal
    timestamp: datetime = Field(..., description="Log timestamp (UTC)")
    time_delta_ms: float | None = Field(
        None, description="Milliseconds since previous log in sequence"
    )

    # System context
    system: str = Field(..., description="SAP system ID")
    host: str | None = Field(None, description="Hostname")
    instance: str | None = Field(None, description="SAP instance number")

    # Component context
    component: str | None = Field(None, description="SAP component (BASIS, ABAP, etc.)")
    work_process_type: str | None = Field(
        None, description="Work process type (DIA, BGD, UPD, etc.)"
    )
    work_process_id: str | None = Field(None, description="Work process ID")

    # Transaction context
    transaction: str | None = Field(None, description="SAP transaction code")
    program: str | None = Field(None, description="ABAP program name")
    user: str | None = Field(None, description="SAP username")

    # Message content
    severity: str = Field(..., description="Log severity level")
    message_class: str | None = Field(None, description="SAP message class")
    message_id: str | None = Field(None, description="SAP message ID")
    raw_message: str = Field(..., description="Original log message text")

    # Derived fields
    error_category: ErrorCategory = Field(
        ErrorCategory.UNKNOWN, description="Classified error category"
    )
    message_signature: str = Field(..., description="Normalized hash/signature for clustering")

    # Correlation
    correlation_ids: dict[str, str] = Field(
        default_factory=dict,
        description="Extracted correlation IDs (RFC TID, session ID, etc.)",
    )


class SampleLogEntry(BaseModel):
    """Compact sample log entry for cluster output.

    Avoids serializing the full ``NormalizedLog`` (which carries many nullable context fields) —
    only the fields relevant for LLM reasoning and audit are included.
    """

    doc_id: str = Field(..., description="Elasticsearch document ID")
    timestamp: datetime = Field(..., description="Log timestamp (UTC)")
    severity: str = Field(..., description="Log severity level")
    raw_message: str = Field(..., description="Original log message text")
    error_category: ErrorCategory = Field(
        ErrorCategory.UNKNOWN, description="Classified error category"
    )


# =============================================================================
# CLUSTER + ANOMALY
# =============================================================================


class LogCluster(BaseModel):
    """A cluster of related log entries.

    Logs are grouped by signature similarity, temporal proximity, and system/component alignment.
    """

    cluster_id: str = Field(..., description="Unique cluster identifier")
    signature: str = Field(..., description="Representative message signature")
    signature_pattern: str = Field(..., description="Human-readable pattern (with placeholders)")

    # Statistics
    occurrence_count: int = Field(..., description="Number of logs in cluster")
    unique_systems: list[str] = Field(..., description="Distinct systems affected")
    unique_hosts: list[str] = Field(default_factory=list, description="Distinct hosts")
    unique_users: list[str] = Field(default_factory=list, description="Distinct users")

    # Temporal
    first_seen: datetime = Field(..., description="Earliest log in cluster")
    last_seen: datetime = Field(..., description="Latest log in cluster")
    avg_interval_seconds: float | None = Field(None, description="Average time between occurrences")

    # Severity distribution
    severity_distribution: dict[str, int] = Field(
        default_factory=dict, description="Count per severity level"
    )
    dominant_severity: str = Field(..., description="Most common severity")
    error_category: ErrorCategory = Field(..., description="Dominant error category")

    # Component distribution
    component_distribution: dict[str, int] = Field(
        default_factory=dict, description="Count per component"
    )
    transaction_distribution: dict[str, int] = Field(
        default_factory=dict, description="Count per transaction"
    )

    # Sample logs (compact entries — see SampleLogEntry)
    sample_logs: list[SampleLogEntry] = Field(
        default_factory=list, description="Representative sample logs (compact, max 5)"
    )
    sample_doc_ids: list[str] = Field(
        default_factory=list, description="All document IDs in cluster"
    )


class AnomalyFinding(BaseModel):
    """A detected anomaly in the log data."""

    anomaly_id: str = Field(..., description="Unique anomaly identifier")
    anomaly_type: AnomalyType = Field(..., description="Type of anomaly detected")

    # Description
    description: str = Field(..., description="Human-readable anomaly description")
    severity: str = Field(..., description="Anomaly severity (critical, warning, info)")

    # Evidence
    affected_clusters: list[str] = Field(default_factory=list, description="Related cluster IDs")
    metric_value: float | None = Field(default=None, description="Measured value")
    baseline_value: float | None = Field(default=None, description="Expected baseline")
    deviation_percent: float | None = Field(
        default=None, description="Percentage deviation from baseline"
    )

    # Temporal
    detected_at: datetime = Field(..., description="When anomaly was detected")
    window_start: datetime | None = Field(default=None, description="Anomaly window start")
    window_end: datetime | None = Field(default=None, description="Anomaly window end")

    # Context
    systems_affected: list[str] = Field(default_factory=list)
    components_affected: list[str] = Field(default_factory=list)


__all__ = [
    "AnomalyFinding",
    "AnomalyType",
    "ErrorCategory",
    "HypothesisConfidence",
    "LogCluster",
    "LogSeverity",
    "NormalizedLog",
    "SampleLogEntry",
]
