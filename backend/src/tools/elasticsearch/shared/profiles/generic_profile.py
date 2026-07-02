"""``GenericProfile`` — cross-system-type defaults.

Keywords and patterns only cover concepts every profile would agree on: DB, TIMEOUT, MEMORY,
LOCK, AUTHORIZATION, NETWORK, CONFIG, BATCH. No SAP-specific dialect — that profile layers its
own signals on top.
"""

from __future__ import annotations

import re

from src.tools.elasticsearch.shared.profiles.base import (
    HypothesisTemplate,
    LogInvestigationProfile,
)
from src.tools.elasticsearch.shared.schemas import ErrorCategory

_GENERIC_QUICK_KEYWORDS: frozenset[str] = frozenset(
    [
        # Database
        "sql",
        "deadlock",
        "database",
        # Timeout
        "timeout",
        "timed out",
        "time_out",
        "deadline exceeded",
        # Memory
        "memory",
        "heap",
        "oom",
        "outofmemory",
        # Lock
        "lock",
        "locked",
        # Authorization
        "auth",
        "authorization",
        "permission denied",
        "access denied",
        "unauthorized",
        "forbidden",
        # Network
        "connection refused",
        "connection reset",
        "dns",
        "tls",
        "ssl",
        "certificate",
        "host unreachable",
        "http",
        # Config
        "configuration",
    ]
)


_GENERIC_CATEGORY_RULES: list[tuple[ErrorCategory, list[re.Pattern[str]]]] = [
    (
        ErrorCategory.DB,
        [
            re.compile(r"database\s+(?:error|failure|connection)", re.IGNORECASE),
            re.compile(r"\bSQL\s+(?:error|failure|exception)", re.IGNORECASE),
            re.compile(r"deadlock", re.IGNORECASE),
        ],
    ),
    (
        ErrorCategory.TIMEOUT,
        [
            re.compile(r"timeout", re.IGNORECASE),
            re.compile(r"timed?\s*out", re.IGNORECASE),
            re.compile(r"TIME_OUT", re.IGNORECASE),
            re.compile(r"deadline\s+exceeded", re.IGNORECASE),
        ],
    ),
    (
        ErrorCategory.MEMORY,
        [
            re.compile(r"(?:out\s+of|no)\s+memory", re.IGNORECASE),
            re.compile(r"memory\s+(?:exceeded|allocation|error)", re.IGNORECASE),
            re.compile(r"\bOOM(?:Killed)?\b", re.IGNORECASE),
            re.compile(r"OutOfMemoryError", re.IGNORECASE),
            re.compile(r"heap\s+(?:space|exhausted)", re.IGNORECASE),
        ],
    ),
    (
        ErrorCategory.LOCK,
        [
            re.compile(r"lock\s+(?:error|failure|timeout|wait)", re.IGNORECASE),
            re.compile(r"concurrent\s+modification", re.IGNORECASE),
        ],
    ),
    (
        ErrorCategory.AUTHORIZATION,
        [
            re.compile(r"authori[sz]ation\s+(?:error|failure|check)", re.IGNORECASE),
            re.compile(r"permission\s+denied", re.IGNORECASE),
            re.compile(r"access\s+denied", re.IGNORECASE),
            re.compile(r"\bunauthorized\b", re.IGNORECASE),
            re.compile(r"\bforbidden\b", re.IGNORECASE),
        ],
    ),
    (
        ErrorCategory.NETWORK,
        [
            re.compile(r"connection\s+refused", re.IGNORECASE),
            re.compile(r"connection\s+reset", re.IGNORECASE),
            re.compile(r"host\s+unreachable", re.IGNORECASE),
            re.compile(r"DNS\s+(?:lookup|resolution)\s+(?:failed|failure)", re.IGNORECASE),
            re.compile(r"TLS\s+handshake", re.IGNORECASE),
            re.compile(r"SSL\s+(?:error|failure)", re.IGNORECASE),
            re.compile(r"certificate\s+(?:expired|invalid|verify\s+failed)", re.IGNORECASE),
        ],
    ),
    (
        ErrorCategory.CONFIG,
        [
            re.compile(r"configuration\s+(?:error|missing|invalid)", re.IGNORECASE),
            re.compile(r"missing\s+(?:configuration|config|setting)", re.IGNORECASE),
        ],
    ),
]


_GENERIC_VARIABLE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"\b\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b"),
        "<TIMESTAMP>",
    ),
    (re.compile(r"\b\d{2}/\d{2}/\d{4}\b"), "<DATE>"),
    (re.compile(r"\b\d{2}:\d{2}:\d{2}\b"), "<TIME>"),
    (
        re.compile(r"\b[A-F0-9]{8}(?:-[A-F0-9]{4}){3}-[A-F0-9]{12}\b", re.IGNORECASE),
        "<UUID>",
    ),
    (re.compile(r"\b[A-F0-9]{32}\b", re.IGNORECASE), "<GUID>"),
    (re.compile(r"\b0x[A-F0-9]+\b", re.IGNORECASE), "<HEX>"),
    (re.compile(r"\b\d{6,}\b"), "<NUMBER>"),
    (re.compile(r"\bPID[:\s]*\d+\b", re.IGNORECASE), "PID<N>"),
    (re.compile(r"\b(?:session|SESSION)[:\s]*[A-F0-9-]+\b", re.IGNORECASE), "SESSION<ID>"),
]


_GENERIC_SYMPTOM_MAP: dict[str, list[ErrorCategory]] = {
    "slow": [ErrorCategory.TIMEOUT, ErrorCategory.DB, ErrorCategory.MEMORY, ErrorCategory.LOCK],
    "performance": [ErrorCategory.TIMEOUT, ErrorCategory.DB, ErrorCategory.MEMORY],
    "timeout": [ErrorCategory.TIMEOUT],
    "hang": [ErrorCategory.TIMEOUT, ErrorCategory.LOCK],
    "response time": [ErrorCategory.TIMEOUT, ErrorCategory.DB],
    "connection": [ErrorCategory.NETWORK, ErrorCategory.DB],
    "network": [ErrorCategory.NETWORK],
    "dns": [ErrorCategory.NETWORK],
    "certificate": [ErrorCategory.NETWORK],
    "login": [ErrorCategory.AUTHORIZATION],
    "permission": [ErrorCategory.AUTHORIZATION],
    "authorization": [ErrorCategory.AUTHORIZATION],
    "auth": [ErrorCategory.AUTHORIZATION],
    "access denied": [ErrorCategory.AUTHORIZATION],
    "database": [ErrorCategory.DB],
    "db error": [ErrorCategory.DB],
    "sql": [ErrorCategory.DB],
    "deadlock": [ErrorCategory.DB, ErrorCategory.LOCK],
    "memory": [ErrorCategory.MEMORY],
    "out of memory": [ErrorCategory.MEMORY],
    "oom": [ErrorCategory.MEMORY],
    "heap": [ErrorCategory.MEMORY],
    "lock": [ErrorCategory.LOCK],
    "contention": [ErrorCategory.LOCK],
    "config": [ErrorCategory.CONFIG],
    "configuration": [ErrorCategory.CONFIG],
    "job": [ErrorCategory.BATCH],
    "batch": [ErrorCategory.BATCH],
    "scheduled": [ErrorCategory.BATCH],
}


_GENERIC_HYPOTHESES: dict[ErrorCategory, HypothesisTemplate] = {
    ErrorCategory.DB: {
        "template": "Database errors suggest data-layer issues",
        "investigation_hints": [
            "Check DB health dashboards and alert history",
            "Review recent slow / expensive queries",
            "Inspect active locks and blocked sessions",
        ],
    },
    ErrorCategory.TIMEOUT: {
        "template": "Timeouts indicate a performance bottleneck or stuck dependency",
        "investigation_hints": [
            "Identify the slow upstream or downstream call",
            "Check resource saturation (CPU / memory / connection pool)",
            "Review request-timing percentiles over the window",
        ],
    },
    ErrorCategory.MEMORY: {
        "template": "Memory errors indicate resource exhaustion",
        "investigation_hints": [
            "Review heap / RSS trend over time",
            "Check for leak suspects (object growth, unbounded caches)",
            "Compare pod / process memory limits to actual usage",
        ],
    },
    ErrorCategory.LOCK: {
        "template": "Lock / contention errors indicate concurrent-write conflicts",
        "investigation_hints": [
            "Enumerate long-held locks and their holders",
            "Check for lock-wait chains across sessions",
            "Review batch scheduling that may contend on hot rows",
        ],
    },
    ErrorCategory.AUTHORIZATION: {
        "template": "Authorization failures indicate access / role mismatches",
        "investigation_hints": [
            "Inspect the failing principal's effective roles",
            "Check for recent IAM / RBAC policy changes",
            "Confirm token / credential validity",
        ],
    },
    ErrorCategory.NETWORK: {
        "template": "Network errors indicate connectivity or TLS problems",
        "investigation_hints": [
            "Test reachability from the caller (telnet / curl / dig)",
            "Check certificate expiry and chain validation",
            "Review firewall / security-group changes",
        ],
    },
    ErrorCategory.CONFIG: {
        "template": "Configuration errors indicate a missing or invalid setting",
        "investigation_hints": [
            "Diff the running config against the last known-good version",
            "Check for environment variables and secrets presence",
            "Verify schema-level config validation output",
        ],
    },
    ErrorCategory.BATCH: {
        "template": "Scheduled / batch job failure",
        "investigation_hints": [
            "Inspect job logs and exit codes",
            "Check for upstream data-dependency failures",
            "Review the scheduler state and retries",
        ],
    },
    ErrorCategory.UNKNOWN: {
        "template": "Unclassified errors require manual investigation",
        "investigation_hints": [
            "Review raw log messages for recurring patterns",
            "Cross-reference with recent deploys / config changes",
            "Consult vendor / product documentation for the failing component",
        ],
    },
}


class GenericProfile(LogInvestigationProfile):
    """Cross-system default profile. Safe fallback when ``system_type`` is unknown."""

    system_type = "*"

    def quick_keywords(self) -> frozenset[str]:
        return _GENERIC_QUICK_KEYWORDS

    def category_rules(self) -> list[tuple[ErrorCategory, list[re.Pattern[str]]]]:
        return _GENERIC_CATEGORY_RULES

    def variable_signature_patterns(self) -> list[tuple[re.Pattern[str], str]]:
        return _GENERIC_VARIABLE_PATTERNS

    def symptom_category_map(self) -> dict[str, list[ErrorCategory]]:
        return _GENERIC_SYMPTOM_MAP

    def hypothesis_templates(self) -> dict[ErrorCategory, HypothesisTemplate]:
        return _GENERIC_HYPOTHESES


__all__ = ["GenericProfile"]
