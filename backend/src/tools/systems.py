"""``list_sap_systems`` — the managed SAP systems the copilot may reason about.

**Security boundary.** The committed ``systems.yaml`` carries connection details
(``host``/``user``/``sysnr``/``client``) and — before the loader strips it — a ``password``.
None of that may reach the LLM or the UI. This tool therefore returns **only** the fields a user
needs to pick a system: ``name``, ``display_name``, ``environment``. Any new field added to
``SystemConfig`` is *not* exposed here unless it is explicitly added to the projection below.
"""

from __future__ import annotations

from langchain_core.tools import tool

from src.config import get_systems


@tool("list_sap_systems")
def list_sap_systems() -> list[dict[str, str | None]]:
    """List the managed SAP systems available to query (name, display name, environment).

    Use this to resolve which systems exist and their ids before querying metrics. Connection
    details (host, user, credentials) are intentionally never returned.
    """
    return [
        {
            "name": system.name,
            "display_name": system.display_name,
            "environment": system.environment,
        }
        for system in get_systems().values()
    ]
