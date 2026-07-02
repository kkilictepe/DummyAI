"""``SapProfile`` — SAP (and HANA) classification profile.

Carries every SAP-specific signal the deterministic normalizer needs: SAP regex patterns,
category rules (DUMP/RFC/ICM/GATEWAY/TRANSPORT), the ``system_function_type`` (SFT) RFC/BTC/BGD
map, the ``sap.*`` log_name map, SAP severity codes, work-process / transaction / program
extraction, SAP-centric hypothesis templates, and the DB->RFC / MEMORY->DUMP cascade chain.
"""

from __future__ import annotations

import re
from typing import Any

from src.tools.elasticsearch.shared.profiles.base import (
    HypothesisTemplate,
    LogInvestigationProfile,
    extract_field,
)
from src.tools.elasticsearch.shared.schemas import ErrorCategory

# ---------------------------------------------------------------------------
# Pattern definitions (SAP dialect)
# ---------------------------------------------------------------------------

SAP_PATTERNS: dict[str, re.Pattern[str]] = {
    # Short dump
    "dump_name": re.compile(r"(?:ABAP\s+)?(?:short\s+)?dump[:\s]+(\w+)", re.IGNORECASE),
    "dump_exception": re.compile(r"exception[:\s]+(\w+)", re.IGNORECASE),
    # RFC
    "rfc_destination": re.compile(r"RFC\s+destination[:\s]+([A-Z0-9_]+)", re.IGNORECASE),
    "rfc_tid": re.compile(r"RFC\s+TID[:\s]+([A-F0-9]+)", re.IGNORECASE),
    "rfc_error": re.compile(
        r"RFC[:\s]+(COMMUNICATION_FAILURE|SYSTEM_FAILURE|RESOURCE_FAILURE)", re.IGNORECASE
    ),
    # Database (SAP DBSL-layer — DB category is cross-system, regex is SAP-shape)
    "db_error_code": re.compile(r"(?:ORA|SQL|DB)[:\-]?\s*(\d+)", re.IGNORECASE),
    "db_table": re.compile(r"(?:table|TABLE)[:\s]+([A-Z0-9_/]+)"),
    # Transaction / Program
    "transaction": re.compile(r"(?:transaction|tcode)[:\s]+([A-Z0-9_]+)", re.IGNORECASE),
    "program": re.compile(r"(?:program|report)[:\s]+([A-Z0-9_/]+)", re.IGNORECASE),
    # Work process
    "wp_type": re.compile(
        r"(?:WP|work\s*process)\s*(?:type)?[:\s]*(DIA|BGD|UPD|ENQ|SPO|BTC)", re.IGNORECASE
    ),
    "wp_number": re.compile(r"(?:WP|work\s*process)\s*(?:no|number)?[:\s]*(\d+)", re.IGNORECASE),
    # User
    "user": re.compile(r"(?:user|USER)[:\s]+([A-Z0-9_]+)"),
    "session_id": re.compile(r"(?:session|SESSION)[:\s]+([A-F0-9-]+)", re.IGNORECASE),
    # ICM / HTTP
    "http_status": re.compile(r"HTTP[/\s]+(?:\d\.\d\s+)?(\d{3})"),
    "icm_error": re.compile(r"ICM[:\s]+(ERROR|WARNING|RESTART)", re.IGNORECASE),
    # Gateway
    "gw_error": re.compile(r"(?:Gateway|GW)[:\s]+(ERROR|FAILURE|TIMEOUT)", re.IGNORECASE),
    # Lock / enqueue
    "lock_object": re.compile(r"(?:lock|enqueue)\s+(?:object|on)[:\s]+([A-Z0-9_]+)", re.IGNORECASE),
    "lock_holder": re.compile(r"(?:held\s+by|locked\s+by|holder)[:\s]+([A-Z0-9_]+)", re.IGNORECASE),
}


# ---------------------------------------------------------------------------
# Classification tiers
# ---------------------------------------------------------------------------

_SAP_CATEGORY_RULES: list[tuple[ErrorCategory, list[re.Pattern[str]]]] = [
    (
        ErrorCategory.SAP_DUMP,
        [
            re.compile(r"short\s+dump", re.IGNORECASE),
            re.compile(r"ABAP\s+dump", re.IGNORECASE),
            re.compile(r"runtime\s+error", re.IGNORECASE),
            re.compile(r"RABAX", re.IGNORECASE),
        ],
    ),
    (
        ErrorCategory.SAP_RFC,
        [
            re.compile(r"RFC\s+(error|failure|exception)", re.IGNORECASE),
            re.compile(r"COMMUNICATION_FAILURE", re.IGNORECASE),
            re.compile(r"SYSTEM_FAILURE", re.IGNORECASE),
            re.compile(r"RFC\s+destination", re.IGNORECASE),
            re.compile(r"Communication\s+error", re.IGNORECASE),
            re.compile(r"CPIC[_\-]?RC", re.IGNORECASE),
            re.compile(r"CPI-C", re.IGNORECASE),
            re.compile(r"NI_\w+_FAILED", re.IGNORECASE),
            re.compile(r"CM_PRODUCT_SPECIFIC_ERROR", re.IGNORECASE),
            re.compile(r"recv\s+failed", re.IGNORECASE),
            re.compile(r"RESOURCE_FAILURE", re.IGNORECASE),
        ],
    ),
    (
        ErrorCategory.DB,
        [
            re.compile(r"(?:ORA|SQL|DBSL)[:\-]\d+", re.IGNORECASE),
            re.compile(r"database\s+(?:error|failure)", re.IGNORECASE),
            re.compile(r"DB\s+(?:error|failure|connection)", re.IGNORECASE),
            re.compile(r"deadlock", re.IGNORECASE),
        ],
    ),
    (
        ErrorCategory.TIMEOUT,
        [
            re.compile(r"timeout", re.IGNORECASE),
            re.compile(r"timed?\s*out", re.IGNORECASE),
            re.compile(r"TIME_OUT", re.IGNORECASE),
        ],
    ),
    (
        ErrorCategory.MEMORY,
        [
            re.compile(r"(?:out\s+of|no)\s+memory", re.IGNORECASE),
            re.compile(r"memory\s+(?:exceeded|allocation|error)", re.IGNORECASE),
            re.compile(r"TSV_TNEW_PAGE_ALLOC_FAILED", re.IGNORECASE),
            re.compile(r"STORAGE_PARAMETERS_WRONG", re.IGNORECASE),
        ],
    ),
    (
        ErrorCategory.LOCK,
        [
            re.compile(r"(?:enqueue|lock)\s+(?:error|failure|timeout|rejected)", re.IGNORECASE),
            re.compile(r"(?:already\s+)?locked", re.IGNORECASE),
            re.compile(r"ENQUEUE_", re.IGNORECASE),
            re.compile(r"lock\s+held", re.IGNORECASE),
        ],
    ),
    (
        ErrorCategory.AUTHORIZATION,
        [
            re.compile(r"authorization\s+(?:error|failure|check)", re.IGNORECASE),
            re.compile(r"no\s+authorization", re.IGNORECASE),
            re.compile(r"AUTH_CHECK", re.IGNORECASE),
            re.compile(r"permission\s+denied", re.IGNORECASE),
        ],
    ),
    (
        ErrorCategory.SAP_ICM,
        [
            re.compile(r"ICM\s+(?:error|failure|restart)", re.IGNORECASE),
            re.compile(r"HTTP\s+(?:error|failure|\d{3})", re.IGNORECASE),
            re.compile(r"icman", re.IGNORECASE),
        ],
    ),
    (
        ErrorCategory.SAP_GATEWAY,
        [
            re.compile(r"Gateway\s+(?:error|failure)", re.IGNORECASE),
            re.compile(r"GW\s+(?:error|failure)", re.IGNORECASE),
            re.compile(r"gwrd", re.IGNORECASE),
        ],
    ),
    (
        ErrorCategory.BATCH,
        [
            re.compile(r"(?:background\s+)?job\s+(?:error|failure|aborted)", re.IGNORECASE),
            re.compile(r"batch\s+(?:error|failure)", re.IGNORECASE),
            re.compile(r"BTCJOB", re.IGNORECASE),
        ],
    ),
    (
        ErrorCategory.SAP_TRANSPORT,
        [
            re.compile(r"transport\s+(?:error|failure)", re.IGNORECASE),
            re.compile(r"import\s+(?:error|failure)", re.IGNORECASE),
            re.compile(r"STMS", re.IGNORECASE),
        ],
    ),
]


_SAP_QUICK_KEYWORDS: frozenset[str] = frozenset(
    [
        # RFC / network
        "rfc",
        "cpic",
        "cpi-c",
        "ni_",
        # Dumps
        "dump",
        "abend",
        "rabax",
        "runtime error",
        "tsv_tnew",
        # Database (DBSL is SAP-layer)
        "database",
        "deadlock",
        "sql",
        "ora-",
        "dbsl",
        # Timeouts / memory
        "timeout",
        "timed out",
        "time_out",
        "memory",
        "heap",
        "oom",
        # Locks
        "lock",
        "enqueue",
        "locked",
        # Authorization
        "authorization",
        "auth",
        "permission denied",
        # ICM / Gateway / Batch / Transport
        "icm",
        "http",
        "icman",
        "gateway",
        "gwrd",
        "batch",
        "btcjob",
        "background job",
        "transport",
        "stms",
    ]
)


SAP_LOG_NAME_CATEGORY_MAP: dict[str, ErrorCategory] = {
    # Webservice / RFC family
    "sap.webservices.error": ErrorCategory.SAP_RFC,
    "sap.trfc.queue": ErrorCategory.SAP_RFC,
    "sap.qrfc.outbound.queue": ErrorCategory.SAP_RFC,
    # Short dumps (ST22)
    "sap.application.shortdumps": ErrorCategory.SAP_DUMP,
    # Database / update errors (SM13)
    "sap.update.error": ErrorCategory.DB,
    # Background jobs (SM37)
    "sap.background.job.aborted.detail": ErrorCategory.BATCH,
    "sap_background_job_aborted_log": ErrorCategory.BATCH,
    "sap_background_job_released_log": ErrorCategory.BATCH,
    "sap_background_job_finished_log": ErrorCategory.BATCH,
    "sap_background_job_running_log": ErrorCategory.BATCH,
    # Audit log (SM20)
    "sap.audit.logs": ErrorCategory.AUTHORIZATION,
}


SAP_SFT_CATEGORY_MAP: dict[str, ErrorCategory] = {
    "RFC": ErrorCategory.SAP_RFC,
    "BTC": ErrorCategory.BATCH,
    "BGD": ErrorCategory.BATCH,
}


# ---------------------------------------------------------------------------
# Severity / signature
# ---------------------------------------------------------------------------

SAP_SEVERITY_OVERRIDES: dict[str, str] = {
    "a": "CRITICAL",  # SAP Abort
    "abort": "CRITICAL",
    "x": "CRITICAL",  # SAP Exception
    "exception": "CRITICAL",
    "s": "INFO",  # SAP Success
    "dump": "CRITICAL",
}

_SAP_VARIABLE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(?:WP|wp)\s*\d+\b"), "WP<N>"),
]


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

_SAP_SYMPTOM_MAP: dict[str, list[ErrorCategory]] = {
    # Performance / slowness — SAP-flavoured
    "long running": [ErrorCategory.TIMEOUT, ErrorCategory.BATCH],
    # Crashes / dumps
    "crash": [ErrorCategory.SAP_DUMP, ErrorCategory.MEMORY],
    "dump": [ErrorCategory.SAP_DUMP],
    "abend": [ErrorCategory.SAP_DUMP],
    "short dump": [ErrorCategory.SAP_DUMP],
    # Connectivity
    "connection": [ErrorCategory.SAP_RFC, ErrorCategory.SAP_ICM, ErrorCategory.SAP_GATEWAY],
    "rfc": [ErrorCategory.SAP_RFC],
    "communication": [ErrorCategory.SAP_RFC, ErrorCategory.SAP_GATEWAY],
    "web": [ErrorCategory.SAP_ICM],
    # Lock / enqueue
    "enqueue": [ErrorCategory.LOCK],
    # Batch
    "background": [ErrorCategory.BATCH],
    "schedule": [ErrorCategory.BATCH],
    # Transport
    "transport": [ErrorCategory.SAP_TRANSPORT],
    "import": [ErrorCategory.SAP_TRANSPORT],
    # Gateway
    "gateway": [ErrorCategory.SAP_GATEWAY],
}


_SAP_HYPOTHESES: dict[ErrorCategory, HypothesisTemplate] = {
    ErrorCategory.SAP_DUMP: {
        "template": "ABAP short dump indicates application-level failure",
        "investigation_hints": [
            "Check ST22 for dump details",
            "Review ABAP code changes in affected programs",
            "Check memory consumption via SM04/SM66",
        ],
    },
    ErrorCategory.SAP_RFC: {
        "template": "RFC communication failure indicates connectivity or load issues",
        "investigation_hints": [
            "Check SM59 for RFC destination status",
            "Verify network connectivity between systems",
            "Check target system availability and load",
        ],
    },
    ErrorCategory.DB: {
        "template": "Database errors indicate data layer issues",
        "investigation_hints": [
            "Check DB02 for database health",
            "Review expensive SQL statements",
            "Check database locks and deadlocks",
        ],
    },
    ErrorCategory.TIMEOUT: {
        "template": "Timeout errors indicate performance bottlenecks",
        "investigation_hints": [
            "Check work process utilization (SM66)",
            "Review long-running transactions (SM50)",
            "Check enqueue lock waits",
        ],
    },
    ErrorCategory.MEMORY: {
        "template": "Memory errors indicate resource exhaustion",
        "investigation_hints": [
            "Check extended memory usage (ST02)",
            "Review heap memory allocation",
            "Check for memory leaks in custom code",
        ],
    },
    ErrorCategory.LOCK: {
        "template": "Lock/enqueue errors indicate resource contention",
        "investigation_hints": [
            "Check SM12 for lock entries",
            "Review batch job scheduling for conflicts",
            "Check for long-running update tasks",
        ],
    },
    ErrorCategory.AUTHORIZATION: {
        "template": "Authorization failures indicate permission issues",
        "investigation_hints": [
            "Review SU53 for authorization failures",
            "Check user master records (SU01)",
            "Verify role assignments",
        ],
    },
    ErrorCategory.SAP_ICM: {
        "template": "ICM/HTTP errors indicate web layer issues",
        "investigation_hints": [
            "Check SMICM for ICM status",
            "Review HTTP connection limits",
            "Check SSL/TLS certificate validity",
        ],
    },
    ErrorCategory.SAP_GATEWAY: {
        "template": "Gateway errors indicate middleware connectivity issues",
        "investigation_hints": [
            "Check SMGW for gateway status",
            "Review registered RFC servers",
            "Check gateway security settings",
        ],
    },
    ErrorCategory.BATCH: {
        "template": "Batch job errors indicate background processing issues",
        "investigation_hints": [
            "Check SM37 for job logs",
            "Review job scheduling and dependencies",
            "Check background work process availability",
        ],
    },
    ErrorCategory.SAP_TRANSPORT: {
        "template": "Transport errors indicate change management issues",
        "investigation_hints": [
            "Check STMS for transport queue",
            "Review transport logs",
            "Verify transport routes and connections",
        ],
    },
    ErrorCategory.UNKNOWN: {
        "template": "Unclassified errors require manual investigation",
        "investigation_hints": [
            "Review raw log messages for patterns",
            "Check system logs (SM21)",
            "Consult SAP notes for error messages",
        ],
    },
}


# ---------------------------------------------------------------------------
# Cascade pairs (the prior "ABAP" string pointed at nothing — it's SAP_DUMP).
# ---------------------------------------------------------------------------

_SAP_CASCADE_PAIRS: list[tuple[ErrorCategory, ErrorCategory]] = [
    (ErrorCategory.DB, ErrorCategory.SAP_DUMP),
    (ErrorCategory.DB, ErrorCategory.SAP_RFC),
    (ErrorCategory.SAP_RFC, ErrorCategory.SAP_DUMP),
    (ErrorCategory.MEMORY, ErrorCategory.SAP_DUMP),
    (ErrorCategory.LOCK, ErrorCategory.TIMEOUT),
    (ErrorCategory.SAP_GATEWAY, ErrorCategory.SAP_RFC),
    (ErrorCategory.SAP_ICM, ErrorCategory.SAP_RFC),
]


# ---------------------------------------------------------------------------
# Field extractors (SAP aliases)
# ---------------------------------------------------------------------------

_SAP_FIELD_EXTRACTORS: dict[str, list[str]] = {
    "system": ["system_id", "system", "sap_system", "sid"],
    "host": ["host", "hostname", "server", "host.name", "terminal_name"],
    "instance": ["instance", "instance_no", "instance_number", "monitoring_context"],
    "component": ["component", "module", "area", "package"],
    "user": ["user", "username", "user_name", "sap_user"],
}


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


class SapProfile(LogInvestigationProfile):
    """SAP (and HANA) classification profile."""

    system_type = "SAP"

    def quick_keywords(self) -> frozenset[str]:
        return _SAP_QUICK_KEYWORDS

    def category_rules(self) -> list[tuple[ErrorCategory, list[re.Pattern[str]]]]:
        return _SAP_CATEGORY_RULES

    def log_name_category_map(self) -> dict[str, ErrorCategory]:
        return SAP_LOG_NAME_CATEGORY_MAP

    def function_type_category_map(self) -> dict[str, ErrorCategory]:
        return SAP_SFT_CATEGORY_MAP

    def function_type_field_names(self) -> list[str]:
        return ["system_function_type"]

    def severity_map_overrides(self) -> dict[str, str]:
        return SAP_SEVERITY_OVERRIDES

    def variable_signature_patterns(self) -> list[tuple[re.Pattern[str], str]]:
        return _SAP_VARIABLE_PATTERNS

    def field_extractors(self) -> dict[str, list[str]]:
        return _SAP_FIELD_EXTRACTORS

    def extract_component_context(
        self, raw_log: dict[str, Any], message: str
    ) -> dict[str, str | None]:
        wp_type = extract_field(raw_log, ["work_process_type", "wp_type", "process_type", "type"])
        if wp_type:
            wp_type = wp_type.upper()
        else:
            m = SAP_PATTERNS["wp_type"].search(message or "")
            if m:
                wp_type = m.group(1).upper()

        tcode = extract_field(raw_log, ["transaction", "tcode", "transaction_code"])
        if tcode:
            tcode = tcode.upper()
        else:
            m = SAP_PATTERNS["transaction"].search(message or "")
            if m:
                tcode = m.group(1).upper()

        program = extract_field(raw_log, ["program", "report", "program_name"])
        if not program:
            m = SAP_PATTERNS["program"].search(message or "")
            if m:
                program = m.group(1)

        wp_id = extract_field(raw_log, ["work_process_id", "wp_id", "wp_no"])
        message_class = extract_field(raw_log, ["message_class", "msg_class", "msgcls"])
        message_id = extract_field(raw_log, ["message_id", "msg_id", "msgno"])

        return {
            "work_process_type": wp_type,
            "work_process_id": wp_id,
            "transaction": tcode,
            "program": program,
            "message_class": message_class,
            "message_id": message_id,
        }

    def symptom_category_map(self) -> dict[str, list[ErrorCategory]]:
        return _SAP_SYMPTOM_MAP

    def hypothesis_templates(self) -> dict[ErrorCategory, HypothesisTemplate]:
        return _SAP_HYPOTHESES

    def cascade_pairs(self) -> list[tuple[ErrorCategory, ErrorCategory]]:
        return _SAP_CASCADE_PAIRS


__all__ = [
    "SAP_LOG_NAME_CATEGORY_MAP",
    "SAP_PATTERNS",
    "SAP_SFT_CATEGORY_MAP",
    "SapProfile",
]
