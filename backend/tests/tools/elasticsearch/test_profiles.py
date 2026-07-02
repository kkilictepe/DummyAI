"""Profile registry, searchable-field derivation, and system_type resolution."""

from __future__ import annotations

from src.tools.elasticsearch.shared.profiles import GenericProfile, SapProfile, get_profile
from src.tools.elasticsearch.shared.system_type import resolve_system_type


def test_get_profile_maps_sap_and_hana_to_sap() -> None:
    assert isinstance(get_profile("SAP"), SapProfile)
    assert isinstance(get_profile("hana"), SapProfile)  # case-insensitive alias
    assert isinstance(get_profile("SAP"), SapProfile)


def test_get_profile_falls_back_to_generic() -> None:
    assert isinstance(get_profile(None), GenericProfile)
    assert isinstance(get_profile(""), GenericProfile)
    assert isinstance(get_profile("DEFAULT"), GenericProfile)
    assert isinstance(get_profile("KAFKA"), GenericProfile)  # unknown -> generic


def test_searchable_fields_derived_from_extractors() -> None:
    sap = SapProfile()
    fields = sap.searchable_fields()
    # Every field_extractors candidate must be searchable (derived, never hand-maintained).
    for candidates in sap.field_extractors().values():
        assert set(candidates).issubset(fields)
    # function_type field is included so must_match on it validates.
    assert "system_function_type" in fields
    assert "system_id" in fields


def test_resolve_system_type_explicit_wins() -> None:
    assert resolve_system_type("KHP", explicit="oracle") == "ORACLE"
    assert resolve_system_type(None, explicit="  sap ") == "SAP"


def test_resolve_system_type_from_systems_yaml() -> None:
    # KHP is defined in config/systems.yaml with system_type: SAP.
    assert resolve_system_type("KHP") == "SAP"


def test_resolve_system_type_unknown_is_default() -> None:
    assert resolve_system_type("NOPE") == "DEFAULT"
    assert resolve_system_type(None) == "DEFAULT"
