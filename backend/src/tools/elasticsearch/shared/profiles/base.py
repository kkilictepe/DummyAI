"""``LogInvestigationProfile`` — per-system-type classification profile.

A profile exposes the semantic data the deterministic normalizer needs to classify logs for one
family of systems (SAP, generic, ...). The retrieval / clustering / normalization pipeline is
shared; only the profile changes per request.

Concrete profiles override only what they carry; the base provides safe empty defaults for every
surface so adding a new profile is additive.
"""

from __future__ import annotations

import re
from typing import Any, TypedDict

from src.tools.elasticsearch.shared.schemas import ErrorCategory


class HypothesisTemplate(TypedDict):
    """Root-cause hypothesis template used by the reporter."""

    template: str
    investigation_hints: list[str]


# ---------------------------------------------------------------------------
# Shared severity base — profiles contribute overrides on top.
# ---------------------------------------------------------------------------

GENERIC_SEVERITY_MAP: dict[str, str] = {
    "debug": "DEBUG",
    "info": "INFO",
    "information": "INFO",
    "warn": "WARNING",
    "warning": "WARNING",
    "error": "ERROR",
    "err": "ERROR",
    "critical": "CRITICAL",
    "fatal": "CRITICAL",
    "e": "ERROR",
    "w": "WARNING",
    "i": "INFO",
}


# ---------------------------------------------------------------------------
# Cross-profile helpers
# ---------------------------------------------------------------------------


def extract_field(
    raw_log: dict[str, Any],
    field_names: list[str],
    default: str | None = None,
) -> str | None:
    """Look up the first populated value from a list of candidate field names.

    Supports dotted nested paths (e.g. ``host.name`` descends into ``host``). Returns ``default``
    when no candidate yields a truthy value.
    """
    for field in field_names:
        if "." in field:
            value: Any = raw_log
            for part in field.split("."):
                if isinstance(value, dict) and part in value:
                    value = value[part]
                else:
                    value = None
                    break
            if value:
                return str(value)
        elif raw_log.get(field):
            return str(raw_log[field])
    return default


# ---------------------------------------------------------------------------
# Profile base
# ---------------------------------------------------------------------------


class LogInvestigationProfile:
    """Profile interface. Override the surfaces your system family needs."""

    system_type: str = "*"

    # Tiered classification data --------------------------------------------

    def quick_keywords(self) -> frozenset[str]:
        """Lowercased substrings that gate the regex tier (fast-fail)."""
        return frozenset()

    def category_rules(self) -> list[tuple[ErrorCategory, list[re.Pattern[str]]]]:
        """Ordered (category, regexes). First match wins."""
        return []

    def log_name_category_map(self) -> dict[str, ErrorCategory]:
        """``log_name`` (ingest source) -> ErrorCategory. Exact-match."""
        return {}

    def function_type_category_map(self) -> dict[str, ErrorCategory]:
        """function-type value -> ErrorCategory (e.g. SAP SFT RFC/BTC/BGD)."""
        return {}

    def function_type_field_names(self) -> list[str]:
        """ES field-name candidates carrying the function-type value."""
        return ["system_function_type"]

    # Retrieval / normalization -------------------------------------------

    def severity_map_overrides(self) -> dict[str, str]:
        """Extra lowercase severity -> canonical level entries (merged onto base)."""
        return {}

    def variable_signature_patterns(self) -> list[tuple[re.Pattern[str], str]]:
        """Regex -> placeholder substitutions for clustering signatures."""
        return []

    def field_extractors(self) -> dict[str, list[str]]:
        """Semantic -> candidate ES field names. Used by the normalizer to fill
        ``NormalizedLog.host`` / ``instance`` / ``user`` / etc.
        """
        return {
            "system": ["system_id", "system"],
            "host": ["host", "hostname", "server", "host.name", "terminal_name"],
            "instance": ["instance", "instance_number", "monitoring_context"],
            "component": ["component", "module", "area", "package"],
            "user": ["user", "username", "user_name"],
        }

    def source_fields_extension(self) -> list[str]:
        """Extra ES ``_source`` fields this profile needs retrieved."""
        return []

    def searchable_fields(self) -> set[str]:
        """All ES field names this profile can filter or retrieve.

        Derived automatically from ``field_extractors()``, ``source_fields_extension()``, and
        ``function_type_field_names()``. Do NOT override this in concrete profiles with a hardcoded
        set — add fields to the appropriate surface method instead, so the set stays in sync with
        what the normalizer actually reads.
        """
        fields: set[str] = set()
        for candidates in self.field_extractors().values():
            fields.update(candidates)
        fields.update(self.source_fields_extension())
        fields.update(self.function_type_field_names())
        return fields

    def extract_component_context(
        self, raw_log: dict[str, Any], message: str
    ) -> dict[str, str | None]:
        """Extract system-specific component signals (work process, transaction, program,
        message class/id, ...). Keys used downstream:

        - ``work_process_type``, ``work_process_id``
        - ``transaction``, ``program``
        - ``user`` (falls back to ``profile.field_extractors["user"]`` when absent)
        - ``message_class``, ``message_id``
        """
        return {}

    # Reporting / investigation ------------------------------------------

    def symptom_category_map(self) -> dict[str, list[ErrorCategory]]:
        """Free-text symptom keyword -> list of relevant categories."""
        return {}

    def hypothesis_templates(self) -> dict[ErrorCategory, HypothesisTemplate]:
        """ErrorCategory -> hypothesis text + investigation hints."""
        return {}

    def cascade_pairs(self) -> list[tuple[ErrorCategory, ErrorCategory]]:
        """Ordered (source, target) category pairs indicating cascade patterns."""
        return []


__all__ = [
    "GENERIC_SEVERITY_MAP",
    "HypothesisTemplate",
    "LogInvestigationProfile",
    "extract_field",
]
