"""``list_sap_systems`` must expose only safe fields — never connection details/credentials."""

from __future__ import annotations

from src.config import get_systems
from src.tools.systems import list_sap_systems

# Fields present in systems.yaml / SystemConfig that must NEVER reach the LLM or UI.
_FORBIDDEN = {"host", "user", "password", "sysnr", "client", "system_type"}


def test_lists_only_name_display_environment() -> None:
    rows = list_sap_systems.invoke({})
    assert rows  # committed systems.yaml has KHP/KBP/PROD01
    for row in rows:
        assert set(row.keys()) == {"name", "display_name", "environment"}
        assert not (_FORBIDDEN & set(row.keys()))


def test_no_connection_values_leak_into_output() -> None:
    # Assert no OUTPUT value equals a real host/user/sysnr/client value from config — so widening
    # the projection would surface a leaked value here. Value-equality (not substring) avoids
    # false positives from short codes that occur inside names (sysnr '01' inside 'PROD01').
    output_values = {value for row in list_sap_systems.invoke({}) for value in row.values()}
    forbidden: set[str] = set()
    for cfg in get_systems().values():
        forbidden.update(v for v in (cfg.host, cfg.user, cfg.sysnr, cfg.client) if v)
    assert forbidden  # sanity: committed systems.yaml actually carries connection details
    assert forbidden.isdisjoint(output_values)


def test_includes_known_systems() -> None:
    names = {row["name"] for row in list_sap_systems.invoke({})}
    assert {"KHP", "KBP", "PROD01"} <= names
