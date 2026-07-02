"""Profile registry for the deterministic Elasticsearch log tools.

A profile carries one ``system_type``'s classification knowledge. The tools resolve the right
profile per request (via an explicit ``system_type`` or a ``system_id`` -> ``systems.yaml`` lookup)
and pass it to the normalizer and clustering engine. Unknown system_types fall back to
``GenericProfile``. HANA uses the same taxonomy as SAP.
"""

from __future__ import annotations

from src.tools.elasticsearch.shared.profiles.base import (
    GENERIC_SEVERITY_MAP,
    HypothesisTemplate,
    LogInvestigationProfile,
    extract_field,
)
from src.tools.elasticsearch.shared.profiles.generic_profile import GenericProfile
from src.tools.elasticsearch.shared.profiles.sap_profile import SapProfile

_GENERIC = GenericProfile()
_SAP = SapProfile()

_PROFILES: dict[str, LogInvestigationProfile] = {
    "*": _GENERIC,
    "DEFAULT": _GENERIC,
    "UNKNOWN": _GENERIC,
    "GENERIC_APPLICATION": _GENERIC,
    "SAP": _SAP,
    "HANA": _SAP,
}


def get_profile(system_type: str | None) -> LogInvestigationProfile:
    """Return the profile for ``system_type``, falling back to ``GenericProfile``.

    Matching is case-insensitive. ``None`` / empty / unknown values all resolve to the generic
    profile, so callers never need to validate.
    """
    if not system_type:
        return _GENERIC
    key = system_type.strip().upper()
    return _PROFILES.get(key, _GENERIC)


__all__ = [
    "GENERIC_SEVERITY_MAP",
    "GenericProfile",
    "HypothesisTemplate",
    "LogInvestigationProfile",
    "SapProfile",
    "extract_field",
    "get_profile",
]
