"""Resolve a system's ``system_type`` from the committed ``systems.yaml`` (no DB).

Precedence: an explicit caller-supplied type wins; otherwise look ``system_id`` up in
``systems.yaml``; otherwise fall back to ``DEFAULT``. The result is always uppercase so it can be
handed straight to :func:`src.tools.elasticsearch.shared.profiles.get_profile`.

Reads only the non-secret :func:`src.config.get_systems` loader — never ``get_settings()`` — so it
stays behind the credential firewall.
"""

from __future__ import annotations

from src.config import get_systems


def resolve_system_type(
    system_id: str | None,
    *,
    explicit: str | None = None,
    default: str = "DEFAULT",
) -> str:
    """Return the effective uppercase ``system_type`` for ``system_id``."""
    if explicit:
        chosen = explicit.strip().upper()
        if chosen:
            return chosen

    if system_id:
        systems = get_systems()
        entry = systems.get(system_id) or systems.get(system_id.strip().upper())
        if entry is not None and entry.system_type:
            return entry.system_type.upper()

    return default.upper()


__all__ = ["resolve_system_type"]
