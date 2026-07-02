"""Log normalization and enrichment for deterministic investigation.

Profile-driven: every system-type-specific signal (keywords, regexes, log_name map, SFT map,
severity overrides, variable patterns, component extractors) is supplied by the active
``LogInvestigationProfile``. This module only owns the tier-1/2/3/4 classification logic,
timestamp and message extraction, severity normalization, and signature generation — all
system-type-agnostic. All operations are deterministic: same input -> same output.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from src.logging import get_logger
from src.tools.elasticsearch.shared.profiles import (
    GENERIC_SEVERITY_MAP,
    GenericProfile,
    LogInvestigationProfile,
    extract_field,
)
from src.tools.elasticsearch.shared.schemas import ErrorCategory, NormalizedLog
from src.tools.elasticsearch.shared.signature_extractor import SignatureExtractor

_log = get_logger(__name__)


class LogNormalizer:
    """Normalizes raw Elasticsearch logs into ``NormalizedLog``.

    Pass a ``LogInvestigationProfile`` to enable system-type-aware classification; default is
    ``GenericProfile`` (cross-system only).
    """

    def __init__(
        self,
        profile: LogInvestigationProfile | None = None,
        signature_variable_patterns: list[str] | None = None,
        custom_error_patterns: dict[str, str] | None = None,
    ) -> None:
        self._extra_variable_patterns: list[str] = list(signature_variable_patterns or [])
        self.custom_error_patterns: dict[str, str] = custom_error_patterns or {}
        resolved = profile or GenericProfile()
        self._sig_extractor = SignatureExtractor(resolved, self._extra_variable_patterns)
        self.set_profile(resolved)

    def set_profile(self, profile: LogInvestigationProfile) -> None:
        """Rebuild all profile-derived state for a new profile. Idempotent."""
        self.profile: LogInvestigationProfile = profile

        # Signature extraction delegated to SignatureExtractor.
        self._sig_extractor.set_profile(profile)
        self.variable_patterns: list[tuple[re.Pattern[str], str]] = (
            self._sig_extractor.variable_patterns
        )

        # Per-category combined classification patterns.
        self._compiled_category_patterns: list[tuple[ErrorCategory, re.Pattern[str]]] = []
        for category, patterns in self.profile.category_rules():
            if not patterns:
                continue
            combined = re.compile(
                "|".join(f"(?:{p.pattern})" for p in patterns),
                re.IGNORECASE,
            )
            self._compiled_category_patterns.append((category, combined))

        # Classification tier data.
        self._log_name_map = self.profile.log_name_category_map()
        self._function_type_map = self.profile.function_type_category_map()
        self._function_type_fields = self.profile.function_type_field_names()

        # Severity map (generic base + profile overrides).
        self._severity_map: dict[str, str] = {
            **GENERIC_SEVERITY_MAP,
            **self.profile.severity_map_overrides(),
        }

        # Field extractors.
        self._field_extractors: dict[str, list[str]] = self.profile.field_extractors()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def normalize_log(
        self,
        raw_log: dict[str, Any],
        doc_id: str,
        index_name: str,
        previous_timestamp: datetime | None = None,
    ) -> NormalizedLog:
        """Normalize a single raw ES log document to ``NormalizedLog``."""
        timestamp = self._extract_timestamp(raw_log)

        time_delta_ms = None
        if previous_timestamp and timestamp:
            delta = (timestamp - previous_timestamp).total_seconds() * 1000
            time_delta_ms = max(0.0, delta)

        raw_message = self._extract_message(raw_log)
        severity = self._normalize_severity(raw_log)

        system = (
            extract_field(
                raw_log, self._field_extractors.get("system", ["system_id", "system"]), "UNKNOWN"
            )
            or "UNKNOWN"
        )
        host = extract_field(raw_log, self._field_extractors.get("host", []))
        instance = extract_field(raw_log, self._field_extractors.get("instance", []))
        component = extract_field(raw_log, self._field_extractors.get("component", []))

        # Component / transaction context — profile-specific.
        ctx = self.profile.extract_component_context(raw_log, raw_message) or {}
        wp_type = ctx.get("work_process_type")
        wp_id = ctx.get("work_process_id")
        transaction = ctx.get("transaction")
        program = ctx.get("program")
        message_class = ctx.get("message_class")
        message_id = ctx.get("message_id")

        user = extract_field(raw_log, self._field_extractors.get("user", []))

        error_category = self._classify_error_category(raw_message, raw_log)
        message_signature = self._generate_signature(raw_message)
        correlation_ids = self._extract_correlation_ids(raw_log, raw_message)

        return NormalizedLog.model_construct(
            doc_id=doc_id,
            index_name=index_name,
            timestamp=timestamp,
            time_delta_ms=time_delta_ms,
            system=system,
            host=host,
            instance=instance,
            component=component,
            work_process_type=wp_type,
            work_process_id=wp_id,
            transaction=transaction,
            program=program,
            user=user,
            severity=severity,
            message_class=message_class,
            message_id=message_id,
            raw_message=raw_message,
            error_category=error_category,
            message_signature=message_signature,
            correlation_ids=correlation_ids,
        )

    def normalize_batch(
        self,
        raw_logs: list[dict[str, Any]],
        doc_ids: list[str],
        index_name: str,
    ) -> list[NormalizedLog]:
        """Normalize a batch with time-delta calculation between consecutive logs."""
        if len(raw_logs) != len(doc_ids):
            raise ValueError("raw_logs and doc_ids must have same length")

        normalized: list[NormalizedLog] = []
        previous_timestamp: datetime | None = None

        for raw_log, doc_id in zip(raw_logs, doc_ids, strict=True):
            normalized_log = self.normalize_log(raw_log, doc_id, index_name, previous_timestamp)
            previous_timestamp = normalized_log.timestamp
            normalized.append(normalized_log)

        return normalized

    def get_signature_pattern(self, message: str) -> str:
        """Return the human-readable signature pattern (with placeholders, not hashed)."""
        normalized = " ".join(message.split())
        for pattern, replacement in self.variable_patterns:
            normalized = pattern.sub(replacement, normalized)
        if len(normalized) > 200:
            normalized = normalized[:197] + "..."
        return normalized

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _extract_timestamp(self, raw_log: dict[str, Any]) -> datetime:
        timestamp_fields = ["@timestamp", "alert_time", "timestamp", "time", "datetime", "log_time"]
        for field in timestamp_fields:
            if raw_log.get(field):
                value = raw_log[field]
                if isinstance(value, datetime):
                    return value
                if isinstance(value, str):
                    return self._parse_timestamp(value)
        _log.warning("normalizer_no_timestamp", detail="using current time")
        return datetime.now(UTC)

    def _parse_timestamp(self, ts_str: str) -> datetime:
        """Parse ES / ISO-8601 / SAP-style timestamp strings, always TZ-aware UTC."""
        # Fast path — ES ISO-8601 (e.g. "2026-03-28T18:02:42.301161Z").
        try:
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt
        except ValueError:
            pass

        # Non-ISO fallbacks (fromisoformat above already handles ISO-8601 + trailing Z). Match each
        # format against the RAW string — no 'Z' mangling, which previously appended a literal 'Z'
        # that the slash/space formats have no directive for, silently dropping them to now().
        formats = [
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
            "%Y/%m/%d %H:%M:%S",
            "%d/%m/%Y %H:%M:%S",
        ]
        for fmt in formats:
            try:
                dt = datetime.strptime(ts_str, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                return dt
            except ValueError:
                continue

        _log.warning("normalizer_unparseable_timestamp", value=ts_str)
        return datetime.now(UTC)

    def _extract_message(self, raw_log: dict[str, Any]) -> str:
        """Extract message text. Prefers ``msg_text`` (real log line) over ``message``."""
        message_fields = [
            "msg_text",
            "alert_details",
            "message",
            "msg",
            "text",
            "log_message",
            "body",
            "content",
        ]
        for field in message_fields:
            if field in raw_log and raw_log[field] is not None:
                value = raw_log[field]
                return str(value)
        return str(raw_log)

    def _normalize_severity(self, raw_log: dict[str, Any]) -> str:
        # Nested log.level (our ES index convention).
        log_obj = raw_log.get("log")
        if isinstance(log_obj, dict) and log_obj.get("level"):
            raw_severity = str(log_obj["level"]).lower().strip()
            return self._severity_map.get(raw_severity, "INFO")

        for field in ("severity", "level", "log_level", "priority"):
            if raw_log.get(field):
                raw_severity = str(raw_log[field]).lower().strip()
                return self._severity_map.get(raw_severity, "INFO")

        return "INFO"

    def _classify_error_category(self, message: str, raw_log: dict[str, Any]) -> ErrorCategory:
        """Classify the error category. Profile-driven; tiers:

        1. Structured error_code / error_type / exception_type regex
        2. Message keyword gate + regex
        3. log_name -> category map
        4. function_type (e.g. SAP SFT) -> category map
        5. UNKNOWN
        """
        # Tier 1 — structured error_code / exception_type.
        error_code = extract_field(raw_log, ["error_code", "error_type", "exception_type"])
        if error_code:
            for category, combined_pat in self._compiled_category_patterns:
                if combined_pat.search(error_code):
                    return category

        # Tier 2 — message regex. Evaluated unconditionally: the profile's quick_keywords set is
        # NOT a superset of its category regexes (e.g. 'recv failed', 'concurrent modification',
        # 'STORAGE_PARAMETERS_WRONG' match a regex but contain no gate keyword), so gating on
        # keywords would silently drop real DB/RFC/MEMORY/LOCK errors to UNKNOWN.
        if message:
            for category, combined_pat in self._compiled_category_patterns:
                if combined_pat.search(message):
                    return category

        # Tier 3 — log_name map.
        if self._log_name_map:
            log_name = extract_field(raw_log, ["log_name"])
            if log_name:
                mapped = self._log_name_map.get(log_name)
                if mapped is not None:
                    return mapped

        # Tier 4 — function_type hint (SAP SFT etc.).
        if self._function_type_map:
            fn_type = extract_field(raw_log, self._function_type_fields)
            if fn_type:
                mapped_ft = self._function_type_map.get(fn_type.upper())
                if mapped_ft is not None:
                    return mapped_ft

        return ErrorCategory.UNKNOWN

    def _generate_signature(self, message: str) -> str:
        """Delegate to SignatureExtractor."""
        return self._sig_extractor.generate_signature(message)

    def _extract_correlation_ids(self, raw_log: dict[str, Any], message: str) -> dict[str, str]:
        """Extract generic correlation identifiers (trace-id, request-id, span-id)."""
        ids: dict[str, str] = {}

        trace = extract_field(
            raw_log, ["trace_id", "trace.id", "x_request_id", "request_id", "correlation_id"]
        )
        if trace:
            ids["trace_id"] = trace

        span = extract_field(raw_log, ["span_id", "span.id"])
        if span:
            ids["span_id"] = span

        return ids
